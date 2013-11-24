import datetime
import email
import re
import time
from imaplib import ParseFlags


class Message():

    def __init__(self, mailbox, uid):
        self.uid = uid
        self.mailbox = mailbox
        self.gmail = mailbox.gmail if mailbox else None

        self.message = None
        self.headers = {}

        self.subject = None
        self.body = None

        self.to = None
        self.fr = None
        self.cc = None
        self.delivered_to = None

        self.sent_at = None

        self.flags = []
        self.labels = []

        self.thread_id = None
        self.thread = []
        self.message_id = None

    def is_read(self):
        return ('\\Seen' in self.flags)

    def read(self):
        flag = '\\Seen'
        self.gmail.imap.uid('STORE', self.uid, '+FLAGS', flag)
        if flag not in self.flags:
            self.flags.append(flag)

    def unread(self):
        flag = '\\Seen'
        self.gmail.imap.uid('STORE', self.uid, '-FLAGS', flag)
        if flag in self.flags:
            self.flags.remove(flag)

    def is_starred(self):
        return ('\\Flagged' in self.flags)

    def star(self):
        flag = '\\Flagged'
        self.gmail.imap.uid('STORE', self.uid, '+FLAGS', flag)
        if flag not in self.flags:
            self.flags.append(flag)

    def unstar(self):
        flag = '\\Flagged'
        self.gmail.imap.uid('STORE', self.uid, '-FLAGS', flag)
        if flag in self.flags:
            self.flags.remove(flag)

    def is_draft(self):
        return ('\\Draft' in self.flags)

    def has_label(self, label):
        full_label = '%s' % label
        return (full_label in self.labels)

    def add_label(self, label):
        full_label = '%s' % label
        self.gmail.imap.uid('STORE', self.uid, '+X-GM-LABELS', full_label)
        if full_label not in self.labels:
            self.labels.append(full_label)

    def remove_label(self, label):
        full_label = '%s' % label
        self.gmail.imap.uid('STORE', self.uid, '-X-GM-LABELS', full_label)
        if full_label in self.labels:
            self.labels.remove(full_label)

    def is_deleted(self):
        return ('\\Deleted' in self.flags)

    def delete(self):
        flag = '\\Deleted'
        self.gmail.imap.uid('STORE', self.uid, '+FLAGS', flag)
        if flag not in self.flags:
            self.flags.append(flag)

        trash = '[Gmail]/Trash' if '[Gmail]/Trash' in self.gmail.labels() else '[Gmail]/Bin'
        if self.mailbox.name not in ['[Gmail]/Bin', '[Gmail]/Trash']:
            self.move_to(trash)

    def move_to(self, name):
        self.gmail.copy(self.uid, name, self.mailbox.name)
        if name not in ['[Gmail]/Bin', '[Gmail]/Trash']:
            self.delete()

    def archive(self):
        self.move_to('[Gmail]/All Mail')

    def decode_header(self, header):
        if header:
            try:
                header.encode('us-ascii')
            except UnicodeDecodeError:
                return header
            else:
                return ''.join(text.decode(charset or 'us-ascii')
                               for text, charset in email.header.decode_header(header))
        else:
            return header

    def parse_headers(self, message):
        hdrs = {}
        for hdr in message.keys():
            hdrs[hdr] = self.decode_header(message[hdr])
        return hdrs

    def parse_addresses(self, addresses):
        if addresses:
            return [formatted
                    for decoded in (self.decode_header(addresses),)
                    for address in decoded.split(',')
                    for parsed in (email.utils.parseaddr(address),)
                    for formatted in (email.utils.formataddr(parsed),)
                    if formatted]
        else:
            return []

    def parse_flags(self, headers):
        return list(ParseFlags(headers))

    def parse_labels(self, headers):
        if re.search(r'X-GM-LABELS \(([^\)]+)\)', headers):
            labels = re.search(r'X-GM-LABELS \(([^\)]+)\)', headers).groups(1)[0].split(' ')
            return map(lambda l: l.replace('"', '').decode("string_escape"), labels)
        else:
            return list()

    def get_charset(self, message=None):
        message = message or self.message
        return message.get_content_charset() or message.get_charset()

    def parse(self, raw_message):
        raw_headers = raw_message[0]
        raw_email = raw_message[1]

        message = email.message_from_string(raw_email)
        self.message = message
        self.headers = self.parse_headers(message)

        froms = self.parse_addresses(message['from'])
        self.fr = froms[0] if froms else ''
        self.to = self.parse_addresses(message['to'])
        self.cc = self.parse_addresses(message['cc'])
        self.delivered_to = self.parse_addresses(message['delivered_to'])

        self.subject = self.decode_header(message['subject'])
        content_type = message.get_content_maintype()
        message_charset = self.get_charset() or 'us-ascii'
        if content_type == "multipart":
            for content in message.walk():
                if content.get_content_type() == "text/plain":
                    charset = self.get_charset(content) or message_charset
                    self.body = content.get_payload(decode=True).decode(charset)
        elif content_type == "text":
            self.body = message.get_payload().decode(message_charset)

        self.sent_at = datetime.datetime.fromtimestamp(time.mktime(email.utils.parsedate_tz(message['date'])[:9]))

        self.flags = self.parse_flags(raw_headers)

        self.labels = self.parse_labels(raw_headers)

        if re.search(r'X-GM-THRID (\d+)', raw_headers):
            self.thread_id = re.search(r'X-GM-THRID (\d+)', raw_headers).groups(1)[0]
        if re.search(r'X-GM-MSGID (\d+)', raw_headers):
            self.message_id = re.search(r'X-GM-MSGID (\d+)', raw_headers).groups(1)[0]

    def fetch(self):
        if not self.message:
            response, results = self.gmail.imap.uid('FETCH', self.uid, '(BODY.PEEK[] FLAGS X-GM-THRID X-GM-MSGID X-GM-LABELS)')

            self.parse(results[0])

        return self.message

    # returns a list of fetched messages (both sent and received) in chronological order
    def fetch_thread(self):
        self.fetch()
        original_mailbox = self.mailbox
        self.gmail.use_mailbox(original_mailbox.name)

        # fetch and cache messages from inbox or other received mailbox
        response, results = self.gmail.imap.uid('SEARCH', None, '(X-GM-THRID ' + self.thread_id + ')')
        received_messages = {}
        uids = results[0].split(' ')
        if response == 'OK':
            received_messages = {uid: Message(self.gmail.mailboxes['[Gmail]/Sent Mail'], uid) for uid in uids}
            self.gmail.fetch_multiple_messages(received_messages)
            self.mailbox.messages.update(received_messages)

        # fetch and cache messages from 'sent'
        self.gmail.use_mailbox('[Gmail]/Sent Mail')
        response, results = self.gmail.imap.uid('SEARCH', None, '(X-GM-THRID ' + self.thread_id + ')')
        sent_messages = {}
        uids = results[0].split(' ')
        if response == 'OK':
            sent_messages = {uid: Message(self.gmail.mailboxes['[Gmail]/Sent Mail'], uid) for uid in uids}
            self.gmail.fetch_multiple_messages(sent_messages)
            self.gmail.mailboxes['[Gmail]/Sent Mail'].messages.update(sent_messages)

        self.gmail.use_mailbox(original_mailbox.name)

        # combine and sort sent and received messages
        return sorted(dict(received_messages.items() + sent_messages.items()).values(), key=lambda m: m.sent_at)
