"""Retained, double-buffered video images on the existing Tk Canvas.

Fill an offscreen photo before swapping the Canvas item to it. Both photos stay
alive throughout playback, and a resize keeps the old front image until the new
pair is ready. No native child window, background erase, or per-frame clear.
"""
from PIL import ImageTk


class VideoSurface:
    def __init__(self, canvas, item):
        self.canvas = canvas
        self.item = item
        self.photos = []
        self.size = None
        self.front = 0

    def present(self, frame):
        if self.size != frame.size or not self.photos:
            # Keep the old pair attached/alive until the replacement is filled
            # and installed. Merely changing geometry must not blank playback.
            photos = [ImageTk.PhotoImage(frame, master=self.canvas),
                      ImageTk.PhotoImage(frame, master=self.canvas)]
            self.canvas.itemconfigure(self.item, image=photos[0])
            self.photos = photos
            self.size = frame.size
            self.front = 0
        else:
            back = 1-self.front
            self.photos[back].paste(frame)
            self.canvas.itemconfigure(self.item, image=self.photos[back])
            self.front = back

    def close(self):
        self.canvas.itemconfigure(self.item, image='')
        self.photos = []
        self.size = None
