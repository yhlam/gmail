import mimetypes
import os
from email import encoders
from email.header import Header
from email.charset import Charset
from email.mime.audio import MIMEAudio
from email.mime.base import MIMEBase
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import make_msgid, parseaddr, formataddr


__all__ = ['Draft']


def attachment_factory(attachment_type, is_binary):
    mode = 'rb' if is_binary else 'r'

    def factory(filename, subtype):
        with open(filename, mode) as f:
            content = f.read()
            return attachment_type(content, _subtype=subtype)

    return factory


def default_attachment_type(maintype):
    def factory(filename, subtype):
        with open(filename, 'rb') as f:
            content = f.read()
            attachment = MIMEBase(maintype, subtype)
            attachment.set_payload(content)
            encoders.encode_base64(attachment)
            return attachment
    return factory


attachment_types = {
    'text': attachment_factory(MIMEText, False),
    'image': attachment_factory(MIMEImage, True),
    'audio': attachment_factory(MIMEAudio, True)
}


def guess_charset(content):
    try:
        content.encode('us-ascii')
        return 'us-ascii'
    except (UnicodeDecodeError, UnicodeEncodeError):
        return 'utf-8'


class Draft(object):
    def __init__(self, gmail, sender, recipients, subject, plain=None, html=None, cc=None, bcc=None, attachments=None, headers=None):
        if plain is None and html is None:
            raise ValueError('plain and html cannot be None at the same time')

        cc = cc or []
        bcc = bcc or []
        attachments = attachments or []
        headers = headers or {}

        self.gmail = gmail
        self.sender = parseaddr(sender)[1]
        self.recipients = recipients + cc + bcc
        self.message = MIMEMultipart()
        self._populate_header(subject, sender, recipients, cc, headers)
        self._populate_content(plain, html)
        self._attach(attachments)

    def _populate_header(self, subject, sender, recipients, cc, headers):
        def populate_addresses(field, addresses):
            def encode_address(addr):
                name, email = parseaddr(addr)
                charset = Charset(guess_charset(name))
                encoded_name = charset.header_encode(name)
                return formataddr((encoded_name, email))

            if addresses:
                self.message[field] = ', '.join(map(encode_address, addresses))

        self.message = MIMEMultipart()
        self.message['Subject'] = Header(subject, guess_charset(subject))
        self.message['From'] = sender
        populate_addresses('To', recipients)
        populate_addresses('Cc', cc)
        self.message['Message-ID'] = make_msgid()
        for key, value in headers.items():
            self.message[key] = value

    def _populate_content(self, plain, html):
        plain_msg = None if plain is None else MIMEText(plain, _subtype='plain', _charset=guess_charset(plain))
        html_msg = None if html is None else MIMEText(html, _subtype='html', _charset=guess_charset(html))

        if plain_msg and html_msg:
            body = MIMEMultipart('alternative')
            body.attach(plain_msg)
            body.attach(html_msg)
        else:
            body = plain_msg or html_msg

        self.message.attach(body)

    def _attach(self, attachments):
        for path in attachments:
            directory, filename = os.path.split(path)
            ctype, encoding = mimetypes.guess_type(filename)
            if ctype is None or encoding is not None:
                ctype = 'application/octet-stream'

            maintype, subtype = ctype.split('/', 1)
            factory = attachment_types.get(maintype, default_attachment_type(maintype))
            attachment = factory(path, subtype)
            attachment.add_header('Content-Disposition', 'attachment', filename=filename)
            self.message.attach(attachment)

    def send(self):
        self.gmail.smtp.sendmail(self.sender, self.recipients, self.message.as_string())
        msgid = self.message['Message-ID']
        return msgid
