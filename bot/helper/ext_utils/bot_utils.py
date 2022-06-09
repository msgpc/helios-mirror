from re import match, findall
from threading import Thread, Event
from time import time, sleep
from math import ceil
from psutil import virtual_memory, cpu_percent, disk_usage, cpu_count, net_io_counters
from requests import head as rhead
from urllib.request import urlopen
from telegram import InlineKeyboardMarkup
from telegram.ext import CallbackQueryHandler

from bot.helper.telegram_helper.bot_commands import BotCommands
from bot import download_dict, download_dict_lock, STATUS_LIMIT, botStartTime, LOGGER, status_reply_dict, status_reply_dict_lock, dispatcher, bot, OWNER_ID
from bot.helper.telegram_helper.button_build import ButtonMaker

MAGNET_REGEX = r"magnet:\?xt=urn:btih:[a-zA-Z0-9]*"

URL_REGEX = r"(?:(?:https?|ftp):\/\/)?[\w/\-?=%.]+\.[\w/\-?=%.]+"

COUNT = 0
PAGE_NO = 1


class MirrorStatus:
    STATUS_UPLOADING = "Uploading...📤"
    STATUS_DOWNLOADING = "Downloading...📥"
    STATUS_CLONING = "Cloned...♻️"
    STATUS_WAITING = "Queued...💤"
    STATUS_FAILED = "Failed 🚫. Cleaning Download..."
    STATUS_PAUSE = "Paused...⛔️"
    STATUS_ARCHIVING = "Archiving...🔐"
    STATUS_EXTRACTING = "Extracting...📂"
    STATUS_SPLITTING = "Splitting...✂️"
    STATUS_CHECKING = "Checkingup...📝"
    STATUS_SEEDING = "Seeding...🌧"

SIZE_UNITS = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']


class setInterval:
    def __init__(self, interval, action):
        self.interval = interval
        self.action = action
        self.stopEvent = Event()
        thread = Thread(target=self.__setInterval)
        thread.start()

    def __setInterval(self):
        nextTime = time() + self.interval
        while not self.stopEvent.wait(nextTime - time()):
            nextTime += self.interval
            self.action()

    def cancel(self):
        self.stopEvent.set()

def get_readable_file_size(size_in_bytes) -> str:
    if size_in_bytes is None:
        return '0B'
    index = 0
    while size_in_bytes >= 1024:
        size_in_bytes /= 1024
        index += 1
    try:
        return f'{round(size_in_bytes, 2)}{SIZE_UNITS[index]}'
    except IndexError:
        return 'File too large'

def getDownloadByGid(gid):
    with download_dict_lock:
        for dl in list(download_dict.values()):
            status = dl.status()
            if (
                status
                not in [
                    MirrorStatus.STATUS_ARCHIVING,
                    MirrorStatus.STATUS_EXTRACTING,
                    MirrorStatus.STATUS_SPLITTING,
                ]
                and dl.gid() == gid
            ):
                return dl
    return None

def getAllDownload():
    with download_dict_lock:
        for dlDetails in list(download_dict.values()):
            status = dlDetails.status()
            if (
                status
                not in [
                    MirrorStatus.STATUS_ARCHIVING,
                    MirrorStatus.STATUS_EXTRACTING,
                    MirrorStatus.STATUS_SPLITTING,
                    MirrorStatus.STATUS_CLONING,
                    MirrorStatus.STATUS_UPLOADING,
                    MirrorStatus.STATUS_CHECKING,
                ]
                and dlDetails
            ):
                return dlDetails
    return None

def get_progress_bar_string(status):
    completed = status.processed_bytes() / 8
    total = status.size_raw() / 8
    p = 0 if total == 0 else round(completed * 100 / total)
    p = min(max(p, 0), 100)
    cFull = p // 8
    p_str = '●' * cFull
    p_str += '○' * (12 - cFull)
    p_str = f"[{p_str}]"
    return p_str

def progress_bar(percentage):
    """Returns a progress bar for download
    """
    #percentage is on the scale of 0-1
    comp = '▓'
    ncomp = '░'
    pr = ""

    if isinstance(percentage, str):
        return "NaN"

    try:
        percentage=int(percentage)
    except:
        percentage = 0

    for i in range(1,11):
        if i <= int(percentage/10):
            pr += comp
        else:
            pr += ncomp
    return pr

def get_readable_message():
    with download_dict_lock:
        dlspeed_bytes = 0
        uldl_bytes = 0
        START = 0
        num_active = 0
        num_seeding = 0
        num_upload = 0
        for stats in list(download_dict.values()):
            if stats.status() == MirrorStatus.STATUS_DOWNLOADING:
               num_active += 1
            if stats.status() == MirrorStatus.STATUS_UPLOADING:
               num_upload += 1
            if stats.status() == MirrorStatus.STATUS_SEEDING:
               num_seeding += 1
        if STATUS_LIMIT is not None:
            tasks = len(download_dict)
            global pages
            pages = ceil(tasks/STATUS_LIMIT)
            if PAGE_NO > pages and pages != 0:
                globals()['COUNT'] -= STATUS_LIMIT
                globals()['PAGE_NO'] -= 1
            START = COUNT
        msg = f"<b> Downloading 📤: {num_active} || Uploading 📤: {num_upload} || Seeding 🌧: {num_seeding}</b>\n\n"
        for index, download in enumerate(list(download_dict.values())[START:], start=1):
            reply_to = download.message.reply_to_message
            msg += f"\n• FileName: <code>{download.name()}</code>"
            msg += f"\n• Status: <i>{download.status()}</i>"
            if download.status() not in [
                MirrorStatus.STATUS_ARCHIVING,
                MirrorStatus.STATUS_EXTRACTING,
                MirrorStatus.STATUS_SPLITTING,
                MirrorStatus.STATUS_SEEDING,
            ]:
                msg += f"\n{get_progress_bar_string(download)} {download.progress()}"
                if download.status() == MirrorStatus.STATUS_CLONING:
                    msg += f"\n• Cloned: {get_readable_file_size(download.processed_bytes())} of {download.size()}"
                elif download.status() == MirrorStatus.STATUS_UPLOADING:
                    msg += f"\n• Uploaded: {get_readable_file_size(download.processed_bytes())} of {download.size()}"
                else:
                    msg += f"\n• Downloaded: {get_readable_file_size(download.processed_bytes())} of {download.size()}"
                msg += f"\n• Speed: {download.speed()} | • ETA: {download.eta()}"
                if reply_to:
                    msg += f"\n• Requested By: <a href='tg://user?id={download.message.from_user.id}'>{download.message.from_user.first_name}</a>(<code>{download.message.from_user.id}</code>)"
                else:
                    msg += f"\n• Requested By: <a href='tg://user?id={download.message.from_user.id}'>{download.message.from_user.first_name}</a>(<code>{download.message.from_user.id}</code>)"
                try:
                    msg += f"\n<i>Aria2📶</i> | • Seeders: {download.aria_download().num_seeders}" \
                           f" | • Peers: {download.aria_download().connections}"
                except:
                    pass
                try: 
                    msg += f"\n<i>qbit🦠</i> | • Seeders: {download.torrent_info().num_seeds}" \
                           f" | • Leechers: {download.torrent_info().num_leechs}"
                except:
                    pass
                msg += f"\n• Cancel: <code>/{BotCommands.CancelMirror} {download.gid()}</code>\n________________________________"
            elif download.status() == MirrorStatus.STATUS_SEEDING:
                msg += f"\n• Size: {download.size()}"
                msg += f"\n• Speed: {get_readable_file_size(download.torrent_info().upspeed)}/s"
                msg += f" | • Uploaded: {get_readable_file_size(download.torrent_info().uploaded)}"
                msg += f"\n• Ratio: {round(download.torrent_info().ratio, 3)}"
                msg += f" | • Time: {get_readable_time(download.torrent_info().seeding_time)}"
                msg += f"\n• Cancel: <code>/{BotCommands.CancelMirror} {download.gid()}</code>\n________________________________"
            else:
                msg += f"\n• Size: {download.size()}"
            msg += "\n"
            if STATUS_LIMIT is not None and index == STATUS_LIMIT:
                break
        currentTime = get_readable_time(time() - botStartTime)
        for download in list(download_dict.values()):
            speedy = download.speed()
            if download.status() == MirrorStatus.STATUS_DOWNLOADING:
                if 'K' in speedy:
                    dlspeed_bytes += float(speedy.split('K')[0]) * 1024
                elif 'M' in speedy:
                    dlspeed_bytes += float(speedy.split('M')[0]) * 1048576
            if download.status() == MirrorStatus.STATUS_UPLOADING:
                if 'KB/s' in speedy:
                    uldl_bytes += float(speedy.split('K')[0]) * 1024
                elif 'MB/s' in speedy:
                    uldl_bytes += float(speedy.split('M')[0]) * 1048576
        dlspeed = get_readable_file_size(dlspeed_bytes)
        ulspeed = get_readable_file_size(uldl_bytes)
        msg += f"\n📖 Pages: {PAGE_NO}/{pages} | 📝 Tasks: {tasks}"
        msg += f"\nBOT UPTIME: <code>{currentTime}</code>"
        msg += f"\nDL: {dlspeed}/s🔻 | UL: {ulspeed}/s🔺"
        buttons = ButtonMaker()
        buttons.sbutton("🔄", str(ONE))
        buttons.sbutton("❌", str(TWO))
        buttons.sbutton("📈", str(THREE))
        sbutton = InlineKeyboardMarkup(buttons.build_menu(3))
        if STATUS_LIMIT is not None and tasks > STATUS_LIMIT:
            buttons = ButtonMaker()
            buttons.sbutton("⬅️", "status pre")
            buttons.sbutton("❌", str(TWO))
            buttons.sbutton("➡️", "status nex")
            buttons.sbutton("🔄", str(ONE))
            buttons.sbutton("📈", str(THREE))
            button = InlineKeyboardMarkup(buttons.build_menu(3))
            return msg, button
        return msg, sbutton
                
ONE, TWO, THREE = range(3)
                
def refresh(update, context):
    chat_id  = update.effective_chat.id
    query = update.callback_query
    user_id = update.callback_query.from_user.id
    query.edit_message_text(text="Refreshing...👻")
    sleep(1)
    query.answer(text="Refreshed", show_alert=False)
    
def close(update, context):  
    chat_id  = update.effective_chat.id
    user_id = update.callback_query.from_user.id
    bot = context.bot
    query = update.callback_query
    admins = bot.get_chat_member(chat_id, user_id).status in ['creator', 'administrator'] or user_id in [OWNER_ID] 
    if admins: 
        query.answer()  
        query.message.delete() 
    else:  
        query.answer(text="Nice Try, Get Lost🥱.\n\nOnly Admins can use this.", show_alert=True)
        
def stats(update, context):
    query = update.callback_query
    stats = bot_sys_stats()
    query.answer(text=stats, show_alert=True)

def bot_sys_stats():
    currentTime = get_readable_time(time() - botStartTime)
    cpu = cpu_percent(interval=0.5)
    memory = virtual_memory()
    mem = memory.percent
    total, used, free, disk= disk_usage('/')
    total = get_readable_file_size(total)
    used = get_readable_file_size(used)
    free = get_readable_file_size(free)
    recv = get_readable_file_size(net_io_counters().bytes_recv)
    sent = get_readable_file_size(net_io_counters().bytes_sent)
    stats = f"""
BOT UPTIME: {currentTime}

CPU: {progress_bar(cpu)} {cpu}%
RAM: {progress_bar(mem)} {mem}%
DISK: {progress_bar(disk)} {disk}%

TOTAL: {total}

USED: {used} || FREE: {free}
SENT: {sent} || RECV: {recv}

#KristyCloud
"""
    return stats

def turn(data):
    try:
        with download_dict_lock:
            global COUNT, PAGE_NO
            if data[1] == "nex":
                if PAGE_NO == pages:
                    COUNT = 0
                    PAGE_NO = 1
                else:
                    COUNT += STATUS_LIMIT
                    PAGE_NO += 1
            elif data[1] == "pre":
                if PAGE_NO == 1:
                    COUNT = STATUS_LIMIT * (pages - 1)
                    PAGE_NO = pages
                else:
                    COUNT -= STATUS_LIMIT
                    PAGE_NO -= 1
        return True
    except:
        return False

def get_readable_time(seconds: int) -> str:
    result = ''
    (days, remainder) = divmod(seconds, 86400)
    days = int(days)
    if days != 0:
        result += f'{days}d'
    (hours, remainder) = divmod(remainder, 3600)
    hours = int(hours)
    if hours != 0:
        result += f'{hours}h'
    (minutes, seconds) = divmod(remainder, 60)
    minutes = int(minutes)
    if minutes != 0:
        result += f'{minutes}m'
    seconds = int(seconds)
    result += f'{seconds}s'
    return result
                
def is_url(url: str):
    url = findall(URL_REGEX, url)
    return bool(url)

def is_gdrive_link(url: str):
    return "drive.google.com" in url

def is_gdtot_link(url: str):
    url = match(r'https?://.*\.gdtot\.\S+', url)
    return bool(url)

def is_appdrive_link(url: str):
    url = match(r'https?://(?:\S*\.)?(?:appdrive|driveapp)\.in/\S+', url)
    return bool(url)

def is_mega_link(url: str):
    return "mega.nz" in url or "mega.co.nz" in url

def get_mega_link_type(url: str):
    if "folder" in url:
        return "folder"
    elif "file" in url:
        return "file"
    elif "/#F!" in url:
        return "folder"
    return "file"

def is_magnet(url: str):
    magnet = findall(MAGNET_REGEX, url)
    return bool(magnet)

def new_thread(fn):
    """To use as decorator to make a function call threaded.
    Needs import
    from threading import Thread"""

    def wrapper(*args, **kwargs):
        thread = Thread(target=fn, args=args, kwargs=kwargs)
        thread.start()
        return thread

    return wrapper

def get_content_type(link: str):
    try:
        res = rhead(link, allow_redirects=True, timeout=5)
        content_type = res.headers.get('content-type')
    except:
        content_type = None

    if content_type is None:
        try:
            res = urlopen(link, timeout=5)
            info = res.info()
            content_type = info.get_content_type()
        except:
            content_type = None
    return content_type

dispatcher.add_handler(CallbackQueryHandler(refresh, pattern='^' + str(ONE) + '$'))
dispatcher.add_handler(CallbackQueryHandler(close, pattern='^' + str(TWO) + '$'))
dispatcher.add_handler(CallbackQueryHandler(stats, pattern='^' + str(THREE) + '$'))
