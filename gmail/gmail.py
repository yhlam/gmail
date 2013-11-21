import base64
import imaplib
import re
import smtplib

from .mailbox import Mailbox
from .exceptions import AuthenticationError
from .draft import Draft
from .utf import decode as decode_utf7, encode as encode_utf7


class Gmail():
    # GMail IMAP defaults
    GMAIL_IMAP_HOST = 'imap.gmail.com'
    GMAIL_IMAP_PORT = 993

    # GMail SMTP defaults
    GMAIL_SMTP_HOST = "smtp.gmail.com"
    GMAIL_SMTP_PORT = 587

    def __init__(self):
        self.username = None
        self.password = None
        self.access_token = None

        self.imap = None
        self.smtp = None
        self.logged_in = False
        self.mailboxes = {}
        self.special_mailboxes = {}
        self.current_mailbox = None

        self.imap_connected = False
        self.smtp_connected = False

    def connect(self, raise_errors=True):
        if not self.imap_connected:
            self.imap = imaplib.IMAP4_SSL(self.GMAIL_IMAP_HOST, self.GMAIL_IMAP_PORT)
            self.imap_connected = True

        if not self.smtp_connected:
            self.smtp = smtplib.SMTP(self.GMAIL_SMTP_HOST, self.GMAIL_SMTP_PORT)
            self.smtp.ehlo()
            self.smtp.starttls()
            self.smtp.ehlo()
            self.smtp_connected = True

    @property
    def connected(self):
        return self.imap_connected and self.smtp_connected

    def fetch_mailboxes(self):
        response, mailbox_list = self.imap.list()
        if response == 'OK':
            regex = re.compile(r'\(\\HasNoChildren( \\(?P<special>\w+))?\) "/" "(?P<name>.+)"')
            for mailbox in mailbox_list:
                match = regex.match(mailbox.decode())
                if match:
                    binding = match.groupdict()
                    mailbox_name = decode_utf7(binding['name'])
                    self.mailboxes[mailbox_name] = Mailbox(self, mailbox_name)
                    special = binding['special']
                    if special:
                        self.special_mailboxes[special] = mailbox_name

    def use_mailbox(self, mailbox):
        if mailbox:
            self.imap.select(encode_utf7(mailbox))
        self.current_mailbox = mailbox

    def mailbox(self, mailbox_name):
        mailbox = self.mailboxes.get(mailbox_name)
        if mailbox and not self.current_mailbox == mailbox_name:
            self.use_mailbox(mailbox_name)

        return mailbox

    def special_mailbox(self, mailbox_name):
        real_name = self.special_mailboxes.get(mailbox_name)
        return self.mailbox(real_name)

    def create_mailbox(self, mailbox_name):
        mailbox = self.mailboxes.get(mailbox_name)
        if not mailbox:
            self.imap.create(encode_utf7(mailbox_name))
            mailbox = Mailbox(self, mailbox_name)
            self.mailboxes[mailbox_name] = mailbox

        return mailbox

    def delete_mailbox(self, mailbox_name):
        mailbox = self.mailboxes.get(mailbox_name)
        if mailbox:
            self.imap.delete(encode_utf7(mailbox_name))
            del self.mailboxes[mailbox_name]

    def login(self, username, password):
        username = username if '@' in username else username + '@gmail.com'

        self.username = username
        self.password = password

        if not self.connected:
            self.connect()

        try:
            imap_login = self.imap.login(self.username, self.password)
            imap_logged_in = (imap_login and imap_login[0] == 'OK')
            if imap_logged_in:
                self.fetch_mailboxes()
        except imaplib.IMAP4.error:
            raise AuthenticationError

        try:
            self.smtp.login(self.username, self.password)
            smtp_logged_in = True
        except (smtplib.SMTPHeloError,
                smtplib.SMTPAuthenticationError,
                smtplib.SMTPException):
            raise AuthenticationError

        self.login = imap_logged_in and smtp_logged_in

        return self.logged_in

    def authenticate(self, username, access_token):
        username = username if '@' in username else username + '@gmail.com'

        self.username = username
        self.access_token = access_token

        if not self.connected:
            self.connect()

        auth_string = 'user=%s\1auth=Bearer %s\1\1' % (username, access_token)
        try:
            imap_auth = self.imap.authenticate('XOAUTH2', lambda x: auth_string)
            imap_logged_in = (imap_auth and imap_auth[0] == 'OK')
            if imap_logged_in:
                self.fetch_mailboxes()
        except imaplib.IMAP4.error:
            raise AuthenticationError

        try:
            self.smtp.login('XOAUTH2', base64.b64encode(auth_string))
            smtp_logged_in = True
        except (smtplib.SMTPHeloError,
                smtplib.SMTPAuthenticationError,
                smtplib.SMTPException):
            raise AuthenticationError

        self.login = imap_logged_in and smtp_logged_in

        return self.logged_in

    def logout(self):
        self.imap.logout()
        self.smtp.quit()
        self.smtp_connected = False
        self.logged_in = False

    def label(self, label_name):
        return self.mailbox(label_name)

    def find(self, mailbox_name=None, **kwargs):
        box = self.mailbox(mailbox_name) if mailbox_name else self.all_mail()
        return box.mail(**kwargs)

    def copy(self, uid, to_mailbox, from_mailbox=None):
        if from_mailbox:
            self.use_mailbox(from_mailbox)
        self.imap.uid('COPY', uid, encode_utf7(to_mailbox))

    def fetch_multiple_messages(self, messages):
        fetch_str = ','.join(messages.keys())
        response, results = self.imap.uid('FETCH', fetch_str, '(BODY.PEEK[] FLAGS X-GM-THRID X-GM-MSGID X-GM-LABELS)')
        for index in xrange(len(results) - 1):
            raw_message = results[index]
            if re.search(r'UID (\d+)', raw_message[0]):
                uid = re.search(r'UID (\d+)', raw_message[0]).groups(1)[0]
                messages[uid].parse(raw_message)

        return messages

    def labels(self):
        return self.mailboxes.keys()

    def inbox(self):
        return self.mailbox("INBOX")

    def spam(self):
        return self.special_mailbox("Junk")

    def starred(self):
        return self.special_mailbox("Flagged")

    def all_mail(self):
        return self.special_mailbox("All")

    def sent_mail(self):
        return self.special_mailbox("Sent")

    def important(self):
        return self.special_mailbox("Important")

    def mail_domain(self):
        return self.username.split('@')[-1]

    def send(self, recipients, subject, plain=None, html=None, sender=None, cc=None, bcc=None, attachments=None, headers=None):
        sender = sender or self.username
        draft = Draft(self, sender, recipients, subject, plain, html, cc, bcc, attachments, headers)
        return draft.send()
