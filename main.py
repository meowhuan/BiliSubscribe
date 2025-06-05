# 大量屎山警告
# 仅支持获取链接推送,至少我的技术力不允许我写更多复杂的东西
from ncatbot.plugin import BasePlugin, CompatibleEnrollment
from ncatbot.core import GroupMessage, PrivateMessage, Request
from ncatbot.utils import get_log
import requests
import asyncio
import time
import json
import os
from io import BytesIO
import qrcode
import sqlite3
from cryptography.fernet import Fernet

bot = CompatibleEnrollment
_log = get_log("BiliSubscribe")

class BiliSubscribe(BasePlugin):
    name = "BiliSubscribe"
    version = "1.0.0"
    db_dir = os.path.join(os.path.dirname(__file__), "db")
    db_path = os.path.join(db_dir, "bili_subscribe_cookies.db")
    key_path = os.path.join(db_dir, "bili_subscribe_cookie.key")

    # -------------------- 插件生命周期 --------------------
    async def on_load(self):
        """插件加载时初始化数据库和密钥，并注册功能和定时任务"""
        self._init_db_and_key()
        self.data.setdefault('subscriptions', {})
        self.data.setdefault('last_check', 0)

        # 检查cookie是否存在
        cookies = await self.load_cookies()
        if cookies and cookies.get('SESSDATA'):
            _log.info("BiliSubscribe插件启动成功，已检测到B站cookie。")
        else:
            _log.info("BiliSubscribe插件启动成功，未检测到B站cookie，请先扫码登录。")

        # 注册功能
        self.register_admin_func("login", self.handle_login, prefix="/bili_login")
        self.register_admin_func("add", self.add_subscription, prefix="/bili_sub add", description="添加订阅")
        self.register_admin_func("remove", self.remove_subscription, prefix="/bili_sub remove", description="删除订阅")
        self.register_admin_func("set", self.set_at_all, prefix="/bili_sub set", description="设置@全体")
        self.register_admin_func("list", self.list_subscriptions, prefix="/bili_sub list", description="列出订阅")
        self.register_admin_func("push_now", self.push_now, prefix="/bili_sub push_now", description="立即推送所有订阅最新动态和直播")
        self.register_admin_func("push_dynamic_now", self.push_dynamic_now, prefix="/bili_sub push_dynamic_now", description="立即推送所有订阅最新动态")
        self.register_admin_func("help", self.show_help, prefix="/bili_sub", description="显示所有指令")

        # 启动检查任务
        self.add_scheduled_task(
            self.check_updates,
            "bili_check",
            interval="120s",
            conditions=[lambda: time.time() - self.data['last_check'] > 120]
        )

    async def on_unload(self):
        _log.info("BiliSubscribe插件已卸载")

    def new_method(self):
        self.register_admin_func

    def _init_db_and_key(self):
        """初始化数据库和密钥文件"""
        if not os.path.exists(self.db_dir):
            os.makedirs(self.db_dir, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute(
            '''CREATE TABLE IF NOT EXISTS cookies
               (id INTEGER PRIMARY KEY, data TEXT)'''
        )
        conn.commit()
        conn.close()
        if not os.path.exists(self.key_path):
            key = Fernet.generate_key()
            with open(self.key_path, "wb") as f:
                f.write(key)
        with open(self.key_path, "rb") as f:
            self._fernet = Fernet(f.read())

    async def load_cookies(self):
        """从数据库加载并解密cookies"""
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("SELECT data FROM cookies WHERE id=1")
        row = c.fetchone()
        conn.close()
        if row:
            try:
                decrypted = self._fernet.decrypt(row[0].encode()).decode()
                return json.loads(decrypted)
            except Exception:
                return None
        return None

    async def save_cookies(self, cookies):
        """加密并保存cookies到数据库"""
        encrypted = self._fernet.encrypt(json.dumps(cookies).encode()).decode()
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute("DELETE FROM cookies WHERE id=1")
        c.execute("INSERT INTO cookies (id, data) VALUES (?, ?)", (1, encrypted))
        conn.commit()
        conn.close()

    # -------------------- 登录相关 --------------------
    async def handle_login(self, msg: PrivateMessage):
        """生成B站登录二维码"""
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Referer": "https://passport.bilibili.com/login"
            }
            qr_res = requests.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                headers=headers
            )
            if qr_res.status_code != 200 or "application/json" not in qr_res.headers.get("Content-Type", ""):
                _log.error(f"登录二维码API响应异常: {qr_res.status_code} {qr_res.headers.get('Content-Type')} 内容: {qr_res.text[:200]}")
                await msg.reply(text=f"登录二维码API响应异常，内容：{qr_res.text[:200]}")
                return
            try:
                qr_json = qr_res.json()
            except Exception:
                _log.error(f"二维码API返回内容无法解析为JSON: {qr_res.text[:200]}")
                await msg.reply(text=f"二维码API返回内容无法解析为JSON，内容：{qr_res.text[:200]}")
                return
            qr_data = qr_json.get('data')
            if not qr_data or 'url' not in qr_data or 'qrcode_key' not in qr_data:
                _log.error(f"二维码API返回内容缺少必要字段: {qr_json}")
                await msg.reply(text=f"二维码API返回内容缺少必要字段，内容：{qr_json}")
                return
            qr_url = qr_data['url']
            qrcode_key = qr_data['qrcode_key']

            self.data['login_data'] = {
                'qrcode_key': qrcode_key,
                'expire': time.time() + 180
            }

            qr_img = qrcode.make(qr_url)
            buf = BytesIO()
            qr_img.save(buf, format='PNG')
            buf.seek(0)

            img_dir = "./tmp"
            if not os.path.exists(img_dir):
                os.makedirs(img_dir, exist_ok=True)
            img_path = os.path.join(img_dir, f"bili_qr_{int(time.time())}.png")
            try:
                with open(img_path, "wb") as f:
                    f.write(buf.getvalue())
                await msg.reply(text="请使用B站APP扫描二维码登录（3分钟内有效）")
                await msg.reply(image=img_path)
            except Exception as e:
                _log.error(f"二维码图片保存或发送失败: {str(e)}")
                await msg.reply(text="二维码图片保存或发送失败，请重试")

            asyncio.create_task(self.check_login_status(msg.user_id, qrcode_key))

        except Exception as e:
            _log.error(f"登录失败: {str(e)}")
            await msg.reply(text=f"登录失败: {str(e)}")

    async def check_login_status(self, user_id: str, qrcode_key: str):
        """检查登录状态"""
        try:
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Referer": "https://passport.bilibili.com/login"
            }
            for _ in range(30):
                await asyncio.sleep(7)
                try:
                    check_res = requests.get(
                        f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}",
                        headers=headers
                    )
                    if "application/json" not in check_res.headers.get("Content-Type", ""):
                        _log.error(f"登录状态API返回内容不是JSON: {check_res.text[:200]}")
                        continue
                    status_data = check_res.json()['data']
                except Exception:
                    _log.error(f"登录状态API返回内容无法解析为JSON: {check_res.text[:200]}")
                    continue

                if status_data['code'] == 0:
                    cookies = {
                        'SESSDATA': requests.utils.dict_from_cookiejar(
                            check_res.cookies
                        ).get('SESSDATA', '')
                    }
                    await self.save_cookies(cookies)
                    await self.api.post_private_msg(user_id, text="✅ B站登录成功！")
                    return

                if status_data['code'] == 86038:
                    await self.api.post_private_msg(user_id, text="❌ 二维码已过期，请重新登录")
                    return

            await self.api.post_private_msg(user_id, text="❌ 登录超时，请重试")

        except Exception as e:
            _log.error(f"登录状态检查失败: {str(e)}")
            await self.api.post_private_msg(user_id, text=f"登录状态检查失败: {str(e)}")

    # -------------------- 订阅管理 --------------------
    async def add_subscription(self, msg: GroupMessage):
        """添加B站用户订阅"""
        try:
            args = msg.raw_message.split()
            if len(args) < 3:
                await msg.reply(text="用法: /bili_sub add <UID>")
                return
            uid = args[2]
            group_id = msg.group_id

            user_info = self.get_user_info(uid)
            if not user_info or not user_info.get('name'):
                await msg.reply(text="❌ 用户不存在或获取信息失败")
                return

            if uid not in self.data['subscriptions']:
                self.data['subscriptions'][uid] = {
                    'name': user_info['name'],
                    'live_status': 0,
                    'dynamic_id': 0,
                    'groups': {},
                }

            self.data['subscriptions'][uid]['groups'][group_id] = {
                'live_at_all': False,
                'dynamic_at_all': False
            }

            await msg.reply(text=f"✅ 已订阅用户: {user_info['name']} (UID: {uid})")

        except Exception as e:
            _log.error(f"添加订阅失败: {str(e)}")
            await msg.reply(text=f"添加订阅失败: {str(e)}")

    async def remove_subscription(self, msg: GroupMessage):
        """删除订阅"""
        try:
            args = msg.raw_message.split()
            if len(args) < 3:
                await msg.reply(text="用法: /bili_sub remove <UID>")
                return
            uid = args[2]
            group_id = msg.group_id

            if uid in self.data['subscriptions']:
                if group_id in self.data['subscriptions'][uid]['groups']:
                    del self.data['subscriptions'][uid]['groups'][group_id]
                    if not self.data['subscriptions'][uid]['groups']:
                        del self.data['subscriptions'][uid]
                    await msg.reply(text=f"✅ 已取消订阅: UID {uid}")
                    return

            await msg.reply(text="❌ 未找到对应订阅")

        except Exception as e:
            _log.error(f"删除订阅失败: {str(e)}")
            await msg.reply(text=f"删除订阅失败: {str(e)}")

    async def set_at_all(self, msg: GroupMessage):
        """设置直播/动态@全体成员"""
        try:
            args = msg.raw_message.split()
            if len(args) < 5:
                await msg.reply(text="用法: /bili_sub set <UID> <live/dynamic> <on/off>")
                return
            uid = args[2]
            option = args[3]
            value = args[4].lower() == 'on'

            if uid not in self.data['subscriptions']:
                await msg.reply(text="❌ 未找到订阅")
                return

            group_id = msg.group_id
            if group_id not in self.data['subscriptions'][uid]['groups']:
                await msg.reply(text="❌ 当前群未订阅该用户")
                return

            if option == "live":
                self.data['subscriptions'][uid]['groups'][group_id]['live_at_all'] = value
            elif option == "dynamic":
                self.data['subscriptions'][uid]['groups'][group_id]['dynamic_at_all'] = value
            else:
                await msg.reply(text="❌ 无效选项，可用: live/dynamic")
                return

            await msg.reply(text=f"✅ 已设置 {option} @全体: {'开启' if value else '关闭'}")

        except Exception as e:
            _log.error(f"设置失败: {str(e)}")
            await msg.reply(text=f"设置失败: {str(e)}")

    async def list_subscriptions(self, msg: GroupMessage):
        """列出当前群所有订阅"""
        try:
            group_id = msg.group_id
            subscriptions = []

            for uid, data in self.data['subscriptions'].items():
                if group_id in data['groups']:
                    group_settings = data['groups'][group_id]
                    subscriptions.append(
                        f"{data['name']} (UID: {uid})\n"
                        f"直播@全体: {'✅' if group_settings['live_at_all'] else '❌'}\n"
                        f"动态@全体: {'✅' if group_settings['dynamic_at_all'] else '❌'}"
                    )

            if subscriptions:
                await msg.reply(text="📋 当前群订阅:\n\n" + "\n\n".join(subscriptions))
            else:
                await msg.reply(text="ℹ️ 当前群没有订阅任何B站用户")

        except Exception as e:
            _log.error(f"列出订阅失败: {str(e)}")
            await msg.reply(text=f"列出订阅失败: {str(e)}")

    # -------------------- 推送相关 --------------------
    async def push_now(self, msg: GroupMessage):
        """
        立即获取所有订阅的up最新动态和直播状态并推送（不推送截图）。
        """
        try:
            group_id = msg.group_id
            pushed = False
            for uid, data in self.data['subscriptions'].items():
                if group_id not in data['groups']:
                    continue

                # 推送直播状态（含直播封面）
                live_status, live_cover = self.get_live_status_with_cover(uid)
                if live_status == 1:
                    message = f"📢 {data['name']} 正在直播！\nhttps://live.bilibili.com/{uid}"
                    if data['groups'][group_id]['live_at_all']:
                        message = f"[CQ:at,qq=all] {message}"
                    if live_cover:
                        await msg.reply(image=live_cover)
                    await msg.reply(text=message)
                    pushed = True

                # 推送最新动态（只文本）
                dynamic_id = self.get_latest_dynamic(uid)
                if dynamic_id:
                    dynamic_data = self.get_dynamic_detail(dynamic_id)
                    if dynamic_data:
                        content = dynamic_data.get('content', '')
                        url = f"https://t.bilibili.com/{dynamic_id}"
                        message = f"📢 {data['name']} 最新动态:\n{content}\n{url}"
                        if data['groups'][group_id]['dynamic_at_all']:
                            message = f"[CQ:at,qq=all] {message}"
                        await msg.reply(text=message)
                        pushed = True

            if not pushed:
                await msg.reply(text="当前群没有可推送的直播或动态。")
        except Exception as e:
            _log.error(f"立即推送失败: {str(e)}")
            await msg.reply(text=f"立即推送失败: {str(e)}")

    async def push_dynamic_now(self, msg: GroupMessage):
        """
        立即获取所有订阅的up最新动态并推送（只推送动态，不推送直播）。
        """
        try:
            group_id = msg.group_id
            pushed = False
            for uid, data in self.data['subscriptions'].items():
                if group_id not in data['groups']:
                    continue

                dynamic_id = self.get_latest_dynamic(uid)
                if dynamic_id:
                    dynamic_data = self.get_dynamic_detail(dynamic_id)
                    if dynamic_data:
                        content = dynamic_data.get('content', '')
                        url = f"https://t.bilibili.com/{dynamic_id}"
                        message = f"📢 {data['name']} 最新动态:\n{content}\n{url}"
                        if data['groups'][group_id]['dynamic_at_all']:
                            message = f"[CQ:at,qq=all] {message}"
                        await msg.reply(text=message)
                        pushed = True

            if not pushed:
                await msg.reply(text="当前群没有可推送的动态。")
        except Exception as e:
            _log.error(f"立即推送动态失败: {str(e)}")
            await msg.reply(text=f"立即推送动态失败: {str(e)}")

    # -------------------- 事件推送 --------------------
    async def handle_live_change(self, uid: str, data: dict, new_status: int):
        """处理直播状态变化"""
        user_name = data['name']

        for group_id, settings in data['groups'].items():
            if new_status == 1:
                message = f"📢 {user_name} 开播啦！\nhttps://live.bilibili.com/{uid}"
                if settings['live_at_all']:
                    message = f"[CQ:at,qq=all] {message}"
                await self.api.post_group_msg(group_id, text=message)
            elif new_status == 0:
                message = f"📢 {user_name} 下播了 \nhttps://live.bilibili.com/{uid}"
                if settings['live_at_all']:
                    message = f"[CQ:at,qq=all] {message}"
                await self.api.post_group_msg(group_id, text=message)
                pass

    async def handle_new_dynamic(self, uid: str, data: dict, dynamic_id: str):
        """处理新动态"""
        dynamic_data = self.get_dynamic_detail(dynamic_id)
        if not dynamic_data:
            return

        user_name = data['name']
        content = dynamic_data.get('content', '')
        url = f"https://t.bilibili.com/{dynamic_id}"

        for group_id, settings in data['groups'].items():
            message = f"📢 {user_name} 发布了新动态:\n{content}\n{url}"
            if settings['dynamic_at_all']:
                message = f"[CQ:at,qq=all] {message}"
            await self.api.post_group_msg(group_id, text=message)

    async def show_help(self, msg: GroupMessage):
        """显示所有指令说明"""
        help_text = (
            "BiliSubscribe 指令列表：\n"
            "/bili_sub add <UID>         添加B站用户订阅\n"
            "/bili_sub remove <UID>      删除订阅\n"
            "/bili_sub set <UID> <live/dynamic> <on/off>  设置@全体\n"
            "/bili_sub list              列出当前群所有订阅\n"
            "/bili_sub push_now          立即推送所有订阅最新动态和直播\n"
            "/bili_sub push_dynamic_now  立即推送所有订阅最新动态\n"
            "/bili_login                 生成B站登录二维码（仅限root）"
        )
        await msg.reply(text=help_text)

    async def check_updates(self):
        """检查B站用户状态更新"""
        try:
            self.data['last_check'] = time.time()
            for uid, data in self.data['subscriptions'].items():
                # 检查直播状态
                live_status = self.get_live_status(uid)
                if live_status is not None and live_status != data['live_status']:
                    await self.handle_live_change(uid, data, live_status)
                    data['live_status'] = live_status

                # 检查动态更新
                dynamic_id = self.get_latest_dynamic(uid)
                if dynamic_id and dynamic_id != data['dynamic_id']:
                    await self.handle_new_dynamic(uid, data, dynamic_id)
                    data['dynamic_id'] = dynamic_id

            _log.info("B站订阅状态检查完成")
        except Exception as e:
            _log.error(f"状态检查失败: {str(e)}")

    # -------------------- B站API封装 --------------------
    def get_cookies(self):
        # 同步调用异步方法
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 在事件循环中，需用ensure_future
                future = asyncio.ensure_future(self.load_cookies())
                return future.result() if future.done() else {}
            else:
                return loop.run_until_complete(self.load_cookies())
        except Exception:
            return {}

    def get_user_info(self, uid: str) -> dict:
        """获取用户信息"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Referer": "https://space.bilibili.com/"
            }
            res = requests.get(
                f"https://api.bilibili.com/x/space/acc/info?mid={uid}",
                cookies=self.get_cookies(),
                headers=headers
            )
            if "application/json" not in res.headers.get("Content-Type", ""):
                _log.error(f"获取用户信息API返回内容不是JSON: {res.text[:200]}")
                return None
            data = res.json().get('data', {})
            if not data or 'name' not in data:
                return None
            return {'name': data.get('name', '')}
        except Exception as e:
            _log.error(f"获取用户信息失败: {str(e)}")
            return None

    def get_live_status(self, uid: str) -> int:
        """获取直播状态 (0:未开播, 1:直播中)"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Referer": "https://live.bilibili.com/"
            }
            res = requests.get(
                f"https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld?mid={uid}",
                cookies=self.get_cookies(),
                headers=headers
            )
            if "application/json" not in res.headers.get("Content-Type", ""):
                _log.error(f"获取直播状态API返回内容不是JSON: {res.text[:200]}")
                return None
            return res.json().get('data', {}).get('liveStatus', 0)
        except Exception as e:
            _log.error(f"获取直播状态失败: {str(e)}")
            return None

    def get_live_status_with_cover(self, uid: str):
        """
        获取直播状态和直播封面
        返回: (live_status, cover_url)
        """
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Referer": "https://live.bilibili.com/"
            }
            res = requests.get(
                f"https://api.live.bilibili.com/room/v1/Room/getRoomInfoOld?mid={uid}",
                cookies=self.get_cookies(),
                headers=headers
            )
            if "application/json" not in res.headers.get("Content-Type", ""):
                _log.error(f"获取直播状态API返回内容不是JSON: {res.text[:200]}")
                return 0, None
            data = res.json().get('data', {})
            live_status = data.get('liveStatus', 0)
            cover_url = data.get('cover', None)
            return live_status, cover_url
        except Exception as e:
            _log.error(f"获取直播状态和封面失败: {str(e)}")
            return 0, None

    def get_latest_dynamic(self, uid: str) -> str:
        """获取最新动态ID"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Referer": "https://space.bilibili.com/"
            }
            res = requests.get(
                f"https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/space_history?host_uid={uid}",
                cookies=self.get_cookies(),
                headers=headers
            )
            if "application/json" not in res.headers.get("Content-Type", ""):
                _log.error(f"获取最新动态API返回内容不是JSON: {res.text[:200]}")
                return None
            dynamics = res.json().get('data', {}).get('cards', [])
            if not dynamics:
                return None
            return str(dynamics[0]['desc']['dynamic_id'])
        except Exception as e:
            _log.error(f"获取最新动态失败: {str(e)}")
            return None

    def get_dynamic_detail(self, dynamic_id: str) -> dict:
        """获取动态详情"""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                "Referer": "https://t.bilibili.com/"
            }
            res = requests.get(
                f"https://api.vc.bilibili.com/dynamic_svr/v1/dynamic_svr/get_dynamic_detail?dynamic_id={dynamic_id}",
                cookies=self.get_cookies(),
                headers=headers
            )
            if "application/json" not in res.headers.get("Content-Type", ""):
                _log.error(f"获取动态详情API返回内容不是JSON: {res.text[:200]}")
                return {}
            card = res.json().get('data', {}).get('card', {})
            if not card:
                return {}
            try:
                card_obj = json.loads(card)
                content = card_obj.get('item', {}).get('content', '')
                return {'content': content}
            except Exception:
                return {'content': ''}
        except Exception as e:
            _log.error(f"获取动态详情失败: {str(e)}")
            return {}
