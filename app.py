import asyncio
import socketio
import click
import sys
import os
from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.styles import Style

class TalkomaticCLI:
    def __init__(self, server_url):
        self.server_url = server_url
        self.sio = socketio.AsyncClient(logger=False, engineio_logger=False)
        self.username = None
        self.location = None
        self.current_room = None
        self.user_id = None
        self.rooms = {}
        self.chat_messages = {}
        self.setup_socket_events()
        self.prompt_style = Style.from_dict({
            'username': '#ansigreen',
            'location': '#ansiyellow',
            'room': '#ansiblue',
            'message': '#ansiwhite',
        })
        self.session = PromptSession(style=self.prompt_style)

    def setup_socket_events(self):
        @self.sio.event
        async def connect():
            print(f"Connected to {self.server_url}")

        @self.sio.event
        async def disconnect():
            print("Disconnected from server")

        @self.sio.on('signin status')
        async def on_signin_status(data):
            if data.get('isSignedIn'):
                self.user_id = data.get('userId')
                print(f"Signed in as {self.username} from {self.location}")
                self.display_help()
            else:
                print("Failed to sign in")

        @self.sio.on('lobby update')
        def on_lobby_update(data):
            self.rooms = {room['id']: room for room in data}
            self.display_rooms()

        @self.sio.on('room joined')
        def on_room_joined(data):
            self.current_room = data['roomId']
            print(f"Joined room: {data['roomName']} (ID: {data['roomId']})")
            self.chat_messages = {}

        @self.sio.on('chat update')
        def on_chat_update(data):
            if data['userId'] != self.user_id:
                self.update_chat_message(data['userId'], data['username'], data.get('diff', {}))

        @self.sio.on('user joined')
        def on_user_joined(data):
            print(f"\n{data['username']} joined the room")

        @self.sio.on('user left')
        def on_user_left(data):
            print(f"\nUser {data} left the room")
            if data in self.chat_messages:
                del self.chat_messages[data]

    def update_chat_message(self, user_id, username, diff):
        if user_id not in self.chat_messages:
            self.chat_messages[user_id] = {'username': username, 'message': ''}

        message = self.chat_messages[user_id]['message']
        if diff.get('type') == 'add':
            index = diff.get('index', len(message))
            message = message[:index] + diff['text'] + message[index:]
        elif diff.get('type') == 'delete':
            index = diff.get('index', 0)
            count = diff.get('count', 0)
            message = message[:index] + message[index + count:]

        self.chat_messages[user_id]['message'] = message
        self.display_chat_messages()

    def display_chat_messages(self):
        # Move cursor to the top of the screen
        sys.stdout.write("\033[H")
        # Clear from cursor to end of screen
        sys.stdout.write("\033[J")
        
        print("Chat Messages:")
        print("--------------")
        for user_id, data in self.chat_messages.items():
            print(f"{data['username']}: {data['message'][:50]}")  # Limit to 50 characters per line
        print("\nType your message or command:")
        sys.stdout.flush()

    def get_prompt_text(self):
        room_text = f"[<room>{self.current_room}</room>]" if self.current_room else ""
        return HTML(f'<username>{self.username}</username>@<location>{self.location}</location>{room_text}> ')

    async def connect(self):
        try:
            await self.sio.connect(self.server_url, transports=['websocket'])
        except Exception as e:
            print(f"Error connecting to the server: {e}")
            print("Please check your internet connection and try again.")
            raise SystemExit(1)

    async def sign_in(self, username, location):
        self.username = username
        self.location = location
        await self.sio.emit('join lobby', {'username': username, 'location': location})

    async def create_room(self, name, room_type, layout):
        await self.sio.emit('create room', {
            'name': name,
            'type': room_type,
            'layout': layout
        })

    async def join_room(self, room_id, access_code=None):
        await self.sio.emit('join room', {'roomId': room_id, 'accessCode': access_code})

    async def leave_room(self):
        if self.current_room:
            await self.sio.emit('leave room')
            self.current_room = None
            self.chat_messages = {}
            print("Left the room")

    async def send_chat_update(self, message):
        if self.current_room:
            current_message = self.chat_messages.get(self.user_id, {}).get('message', '')
            diff = self.get_diff(current_message, message)
            if diff:
                await self.sio.emit('chat update', {'diff': diff})
                self.update_chat_message(self.user_id, self.username, diff)

    def get_diff(self, old_message, new_message):
        if old_message == new_message:
            return None
        
        min_len = min(len(old_message), len(new_message))
        for i in range(min_len):
            if old_message[i] != new_message[i]:
                if len(new_message) > len(old_message):
                    return {'type': 'add', 'text': new_message[i:], 'index': i}
                elif len(new_message) < len(old_message):
                    return {'type': 'delete', 'count': len(old_message) - len(new_message), 'index': i}
                else:
                    return {'type': 'add', 'text': new_message[i:], 'index': i}
        
        if len(new_message) > len(old_message):
            return {'type': 'add', 'text': new_message[min_len:], 'index': min_len}
        else:
            return {'type': 'delete', 'count': len(old_message) - len(new_message), 'index': min_len}

    async def update_lobby(self):
        await self.sio.emit('get rooms')

    def display_rooms(self):
        print("\nAvailable Rooms:")
        print("----------------")
        if not self.rooms:
            print("No rooms available")
        else:
            for room_id, room in self.rooms.items():
                print(f"Room: {room['name']} (ID: {room_id})")
                print(f"Type: {room['type']}, Users: {len(room['users'])}/5")
                print("Users:", ", ".join([f"{user['username']} ({user['location']})" for user in room['users']]))
                print()

    def display_help(self):
        print("\nAvailable commands:")
        print("  rooms - Display available rooms")
        print("  join <room_id> - Join a room")
        print("  create <name> <type> <layout> - Create a new room")
        print("  leave - Leave the current room")
        print("  help - Display this help message")
        print("  quit - Exit the application")
        print("\nWhen in a room, type your message and press Enter to send.")

    async def run(self):
        await self.connect()
        await self.sign_in(self.username, self.location)
        
        while True:
            if self.current_room:
                self.display_chat_messages()
            
            try:
                with patch_stdout():
                    user_input = await self.session.prompt_async(self.get_prompt_text())
            except EOFError:
                break

            if user_input.lower() == 'quit':
                await self.leave_room()
                break
            elif user_input.lower() == 'rooms':
                await self.update_lobby()
            elif user_input.lower().startswith('join '):
                _, room_id = user_input.split(' ', 1)
                await self.join_room(room_id)
            elif user_input.lower().startswith('create '):
                try:
                    _, name, room_type, layout = user_input.split(' ', 3)
                    await self.create_room(name, room_type, layout)
                except ValueError:
                    print("Invalid create command. Usage: create <name> <type> <layout>")
            elif user_input.lower() == 'leave':
                await self.leave_room()
            elif user_input.lower() == 'help':
                self.display_help()
            elif self.current_room:
                await self.send_chat_update(user_input)
            else:
                print("Unknown command. Type 'help' for available commands.")

@click.command()
@click.option('--server', default='https://open.talkomatic.co', help='Socket.IO server URL')
@click.option('--username', prompt='Enter your username', help='Your username')
@click.option('--location', prompt='Enter your location', default='On The Web', help='Your location')
def main(server, username, location):
    cli = TalkomaticCLI(server)
    cli.username = username
    cli.location = location
    try:
        asyncio.get_event_loop().run_until_complete(cli.run())
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
    finally:
        asyncio.get_event_loop().run_until_complete(cli.sio.disconnect())

if __name__ == '__main__':
    main()