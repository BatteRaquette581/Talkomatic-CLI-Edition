import asyncio
import socketio
from prompt_toolkit import Application
from prompt_toolkit.layout import Layout, HSplit, VSplit, DynamicContainer
from prompt_toolkit.widgets import TextArea, Label, Frame
from prompt_toolkit.styles import Style
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.layout.dimension import Dimension
import click
import re

class TalkomaticCLI:
    def __init__(self, server_url):
        self.server_url = server_url
        self.sio = socketio.AsyncClient(logger=False, engineio_logger=False)
        self.username = None
        self.location = None
        self.current_room = None
        self.user_id = None
        self.rooms = {}
        self.chat_messages = {}  # {user_id: {'username': str, 'message': str, 'text_area': TextArea}}
        self.system_messages = []  # List to store system messages
        self.my_message = ''
        self.last_access_code = None  # To store access code used during room creation
        self.style = Style.from_dict({
            'username': '#ansigreen',
            'location': '#ansiyellow',
            'room': '#ansiblue',
            'message': '#ansiwhite',
            'prompt': '#ansicyan',
            'header': 'bold',
            'status': 'reverse',
            'help': 'italic',
            'input_field': '',
            'table.header': 'underline',
            'table.cell': '',
            'system': 'bold #ansired',
        })
        self.setup_socket_events()
        self.create_ui()

    def setup_socket_events(self):
        @self.sio.event
        async def connect():
            self.status_bar.text = f"Connected to {self.server_url}"
            await self.sign_in(self.username, self.location)

        @self.sio.event
        async def disconnect():
            self.status_bar.text = "Disconnected from server"

        @self.sio.on('signin status')
        async def on_signin_status(data):
            if data.get('isSignedIn'):
                self.user_id = data.get('userId')
                self.status_bar.text = f"Signed in as {self.username} from {self.location}"
                await self.update_lobby()
            else:
                self.status_bar.text = "Failed to sign in"

        @self.sio.on('lobby update')
        async def on_lobby_update(data):
            self.rooms = {room['id']: room for room in data}
            self.update_room_list()

        @self.sio.on('room joined')
        async def on_room_joined(data):
            self.current_room = data['roomId']
            self.status_bar.text = f"Joined room: {data['roomName']} (ID: {data['roomId']})"
            self.chat_messages = {}
            self.system_messages = []
            self.my_message = ''
            self.update_prompt()
            self.refresh_chat_display()

        @self.sio.on('chat update')
        async def on_chat_update(data):
            if data['userId'] != self.user_id:
                await self.update_chat_message(data['userId'], data['username'], data.get('diff', {}))

        @self.sio.on('user joined')
        async def on_user_joined(data):
            message = f"{data['username']} joined the room"
            self.append_system_message(message)

        @self.sio.on('user left')
        async def on_user_left(user_id):
            username = self.chat_messages.get(user_id, {}).get('username', 'Unknown User')
            message = f"{username} left the room"
            self.append_system_message(message)
            if user_id in self.chat_messages:
                del self.chat_messages[user_id]
            self.refresh_chat_display()

        @self.sio.on('room created')
        async def on_room_created(room_id):
            await self.join_room(room_id, access_code=self.last_access_code)
            self.last_access_code = None  # Clear the access code after use

        @self.sio.on('access code required')
        async def on_access_code_required():
            self.status_bar.text = "Access code required to join this room."

        @self.sio.on('error')
        async def on_error(message):
            self.status_bar.text = f"Error: {message}"

    def create_ui(self):
        self.help_menu = TextArea(
            text=self.get_help_menu_text(),
            focusable=False,
            style='class:help',
            scrollbar=True,
            width=Dimension(weight=1),
            height=Dimension(preferred=10),
            wrap_lines=False,
        )
        self.room_list_area = TextArea(
            text='No rooms available',
            focusable=False,
            scrollbar=True,
            style='class:room_list_area',
            width=Dimension(weight=1),
            height=Dimension(preferred=10),
            wrap_lines=False,
        )
        self.chat_area_container = DynamicContainer(lambda: self.get_chat_area())
        self.input_field = TextArea(
            height=1,
            prompt='> ',
            multiline=False,
            style='class:input_field'
        )
        self.status_bar = Label(text='Not connected', style='class:status')

        # Key bindings
        self.kb = KeyBindings()

        @self.kb.add('enter')
        async def _(event):
            buffer_text = self.input_field.text.strip()
            if buffer_text.startswith(('rooms', 'join ', 'create ', 'createp ', 'leave', 'help', 'quit')):
                await self.handle_user_input(buffer_text)
                self.input_field.text = ''
            else:
                # Clear the input field on Enter
                self.input_field.text = ''

        @self.kb.add('c-c')
        def _(event):
            event.app.exit()

        # Real-time typing
        self.input_field.buffer.on_text_changed += self.on_input_changed

        self.root_container = HSplit([
            Label(text=HTML('<username>Talkomatic CLI</username>'), style='class:header'),
            VSplit([
                Frame(self.help_menu, title='Help Menu'),
                Frame(self.room_list_area, title='Available Rooms'),
            ], height=Dimension(preferred=10)),
            Frame(self.chat_area_container, title='Chat Messages'),
            Frame(self.input_field, title='Input'),
            self.status_bar,
        ])

        self.layout = Layout(self.root_container)

        self.application = Application(
            layout=self.layout,
            key_bindings=self.kb,
            style=self.style,
            full_screen=True,
            refresh_interval=1/30,  # Refresh at 30 FPS
        )

    def on_input_changed(self, event):
        message = self.input_field.text
        asyncio.ensure_future(self.send_chat_update(message))

    def get_help_menu_text(self):
        help_text = (
            "Available Commands:\n"
            "  rooms                     - Display available rooms\n"
            "  join <room_id>            - Join a room\n"
            "  join <room_id> <code>     - Join a semi-private room with access code\n"
            "  create <name>             - Create a new public room\n"
            "  createp <name> <code>     - Create a semi-private room with access code\n"
            "  leave                     - Leave the current room\n"
            "  help                      - Display this help message\n"
            "  quit                      - Exit the application\n"
            "\nType your message and it will be sent in real-time."
        )
        return help_text

    async def handle_user_input(self, user_input):
        if user_input.lower() == 'quit':
            await self.leave_room()
            self.application.exit()
        elif user_input.lower() == 'rooms':
            await self.update_lobby()
        elif user_input.lower().startswith('join '):
            match = re.match(r'^join\s+(\S+)(?:\s+(\S+))?$', user_input, re.IGNORECASE)
            if match:
                room_id = match.group(1)
                access_code = match.group(2)
                await self.join_room(room_id, access_code=access_code)
            else:
                self.status_bar.text = "Invalid join command. Usage: join <room_id> [access_code]"
        elif user_input.lower().startswith('createp '):
            match = re.match(r'^createp\s+(.+?)\s+(\S+)$', user_input, re.IGNORECASE)
            if match:
                name = match.group(1)
                access_code = match.group(2)
                await self.create_room(name, 'semi-private', 'default', access_code=access_code)
            else:
                self.status_bar.text = "Invalid createp command. Usage: createp <name> <access_code>"
        elif user_input.lower().startswith('create '):
            match = re.match(r'^create\s+(.+)$', user_input, re.IGNORECASE)
            if match:
                name = match.group(1)
                await self.create_room(name, 'public', 'default')
            else:
                self.status_bar.text = "Invalid create command. Usage: create <name>"
        elif user_input.lower() == 'leave':
            await self.leave_room()
        elif user_input.lower() == 'help':
            self.help_menu.text = self.get_help_menu_text()
        else:
            self.status_bar.text = "Unknown command. Type 'help' for available commands."

    def update_prompt(self):
        room_text = f"[{self.current_room}]" if self.current_room else ""
        self.input_field.prompt = HTML(f'<username>{self.username}</username>@<location>{self.location}</location>{room_text}> ')

    def update_room_list(self):
        rooms_text = ''
        if not self.rooms:
            rooms_text = 'No rooms available'
        else:
            for room_id, room in self.rooms.items():
                room_type = room['type']
                rooms_text += f"{room_id}: {room['name']} ({len(room['users'])}/5 users) [{room_type}]\n"
        self.room_list_area.text = rooms_text

    def get_chat_area(self):
        frames = []

        # Add system messages at the top
        for message in self.system_messages:
            system_label = Label(text=message, style='class:system')
            frames.append(system_label)

        # Add chat messages from users
        if not self.chat_messages:
            frames.append(Label(text='No messages yet.'))
        else:
            for user_id, data in self.chat_messages.items():
                username = data['username']
                message_area = data['text_area']
                frame = Frame(message_area, title=username)
                frames.append(frame)
        return HSplit(frames)

    def append_system_message(self, message):
        self.system_messages.append(message)
        self.refresh_chat_display()

    async def update_chat_message(self, user_id, username, diff):
        if user_id not in self.chat_messages:
            message_area = TextArea(
                text='',
                focusable=False,
                scrollbar=True,
                wrap_lines=True,
                style='class:chat_area',
            )
            self.chat_messages[user_id] = {
                'username': username,
                'message': '',
                'text_area': message_area,
            }
            self.refresh_chat_display()
        message = self.chat_messages[user_id]['message']

        if diff.get('type') == 'add':
            index = diff.get('index', len(message))
            message = message[:index] + diff['text'] + message[index:]
        elif diff.get('type') == 'delete':
            index = diff.get('index', 0)
            count = diff.get('count', 0)
            message = message[:index] + message[index + count:]
        elif diff.get('type') == 'replace':
            index = diff.get('index', 0)
            message = message[:index] + diff.get('text', '')
        elif diff.get('type') == 'full-replace':
            message = diff.get('text', '')

        self.chat_messages[user_id]['message'] = message
        self.chat_messages[user_id]['text_area'].text = message
        self.application.invalidate()

    def refresh_chat_display(self):
        # Invalidate the application to trigger a redraw
        self.application.invalidate()

    async def connect(self):
        try:
            await self.sio.connect(self.server_url, transports=['websocket'])
        except Exception as e:
            self.status_bar.text = f"Error connecting to the server: {e}"
            await asyncio.sleep(3)
            raise SystemExit(1)

    async def sign_in(self, username, location):
        await self.sio.emit('join lobby', {'username': username, 'location': location})

    async def create_room(self, name, room_type, layout, access_code=None):
        data = {
            'name': name,
            'type': room_type,
            'layout': layout
        }
        if access_code:
            data['accessCode'] = access_code
        await self.sio.emit('create room', data)
        self.last_access_code = access_code  # Store the access code used

    async def join_room(self, room_id, access_code=None):
        data = {'roomId': room_id}
        if access_code:
            data['accessCode'] = access_code
        await self.sio.emit('join room', data)

    async def leave_room(self):
        if self.current_room:
            await self.sio.emit('leave room')
            self.current_room = None
            self.chat_messages = {}
            self.system_messages = []
            self.refresh_chat_display()
            self.status_bar.text = "Left the room"

    async def send_chat_update(self, message):
        if self.current_room:
            current_message = self.chat_messages.get(self.user_id, {}).get('message', '')
            diff = self.get_diff(current_message, message)
            if diff:
                await self.sio.emit('chat update', {'diff': diff})
                if self.user_id not in self.chat_messages:
                    message_area = TextArea(
                        text='',
                        focusable=False,
                        scrollbar=True,
                        wrap_lines=True,
                        style='class:chat_area',
                    )
                    self.chat_messages[self.user_id] = {'username': self.username, 'message': '', 'text_area': message_area}
                    self.refresh_chat_display()
                self.chat_messages[self.user_id]['message'] = message
                self.chat_messages[self.user_id]['text_area'].text = message
                self.application.invalidate()

    def get_diff(self, old_message, new_message):
        diff = {
            'type': 'full-replace',
            'text': new_message,
        }
        return diff

    async def update_lobby(self):
        await self.sio.emit('get rooms')

    async def run(self):
        await self.connect()
        self.update_prompt()
        await self.application.run_async()

@click.command()
@click.option('--server', default='https://classic.talkomatic.co', help='Socket.IO server URL')
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
        try:
            asyncio.get_event_loop().run_until_complete(cli.sio.disconnect())
        except:
            pass

if __name__ == '__main__':
    main()
