"""Simulated phones with the kit: they keep their own photos (the originals never leave the phone) and answer a ZOOM
request by cropping the original, the way the kit does by itself when a request comes in.

    phone = Phone("+15550120", send)       # send(who, text): where the phone's texts go (the hub, the router)
    message = phone.picture(img)            # P code parts: 128 px, grey
    phone.got("... ZOOM ab 40 50 20 18 C")  # -> crops `ab`'s original, in colour, and sends a Z code back
"""
import random
import threading

from evac import codes, smsimage


class Phone:
    def __init__(self, who, send, delay=2.0):
        self.who, self.send, self.delay = who, send, delay
        self.originals, self.inbox, self.outbox = {}, [], []

    def picture(self, img):
        """The photo as the kit sends it, with a 2-character id the phone chose; the original stays here."""
        ident = random.choice("abcdefghjkmnpqrstuvwxyz") + random.choice("23456789abcdefghjkmnpqrstuvwxyz")
        self.originals[ident] = img
        return codes.parts("P", smsimage.webp(img), ident)

    def text(self, message):
        self.outbox.append(message)
        return self.send(self.who, message)

    def got(self, message):
        self.inbox.append(message)
        m = codes.ZOOM.search(message)
        if m and m.group(1) in self.originals:  # the kit crops the original and sends it back
            if len(m.group(2).split()) == 4:
                reply = codes.answer_zoom(self.originals[m.group(1)], m, m.group(1))[0]
                if self.delay:
                    threading.Timer(self.delay, self.text, (reply,)).start()
                else:
                    return self.text(reply)
