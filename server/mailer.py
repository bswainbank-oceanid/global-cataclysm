"""
Sending email (password reset codes: server/hub.py's request_reset), through an SMTP service set up in
server_data/email.json (git-ignored -- it holds the service's password or key):

    {"host": "smtp.resend.com", "port": 587, "username": "resend", "password": "re_...",
     "from": "Global Cataclysm <noreply@mail.example.com>"}

Port 587 uses STARTTLS, 465 a TLS connection from the start. No file: Mailer.from_file() returns None and
the server says resets aren't available.

    mailer = Mailer.from_file()
    mailer.send_later('ann@example.com', 'Subject', 'Body')   # on a thread: the game never waits for it
"""
import json
import logging
import os
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import make_msgid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG = os.path.join(ROOT, 'server_data', 'email.json')
REQUIRED = ('host', 'port', 'username', 'password', 'from')

logger = logging.getLogger('server.mailer')


class Mailer:
    def __init__(self, config):
        missing = [k for k in REQUIRED if not config.get(k)]
        if missing:
            raise ValueError(f"email settings are missing {', '.join(missing)}")
        self.config = dict(config)

    @classmethod
    def from_file(cls, path=CONFIG):
        """The Mailer email.json describes, or None if there is no such file."""
        if not os.path.exists(path):
            return None
        with open(path, encoding='utf-8') as f:
            return cls(json.load(f))

    def message(self, to, subject, text):
        msg = EmailMessage()
        msg['From'] = self.config['from']
        msg['To'] = to
        msg['Subject'] = subject
        msg['Message-ID'] = make_msgid(domain=self.config['from'].rsplit('@', 1)[-1].strip('> '))
        msg.set_content(text)
        return msg

    def send(self, to, subject, text):
        """Sends one email now (raises on failure)."""
        c = self.config
        port = int(c['port'])
        context = ssl.create_default_context()
        if port == 465:
            server = smtplib.SMTP_SSL(c['host'], port, timeout=30, context=context)
        else:
            server = smtplib.SMTP(c['host'], port, timeout=30)
        with server:
            if port != 465:
                server.starttls(context=context)
            server.login(c['username'], c['password'])
            server.send_message(self.message(to, subject, text))

    def send_later(self, to, subject, text):
        """Sends it on a thread of its own; a failure is logged, not raised (nobody is waiting on it)."""
        def run():
            try:
                self.send(to, subject, text)
                logger.info('sent "%s" to %s', subject, to)
            except Exception:
                logger.exception('could not send "%s" to %s', subject, to)
        threading.Thread(target=run, daemon=True).start()
