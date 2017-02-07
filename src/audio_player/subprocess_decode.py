"""
Play object using the decoder.py module.

decoder.py: decoding of mp3, ogg, wma, ... using an external
            programs through a subprocess (lame, avconv, ffmpeg ...)
            -> https://pypi.python.org/pypi/decoder.py/1.5XB
"""

import os
from time import time
import wave

import decoder

from .interface import log, PlayObjectInterface

___all__ = ('SubprocessDecoderPlayObject',)


class SubprocessDecoderPlayObject(PlayObjectInterface):
    """
    Play object using the decoder.py module (audio decoding is done by
    an external program such as avconv or ffmpeg, and audio data is
    then retrieved in our python process)
    """
    pos = None
    audio_file = None
    _decoder_name = "avconv"  # "lame"
    # "avconv" or "ffmpeg" allows us to seek in the song

    def open(self, path, mono=False):
        """Open the audio resource."""
        self.pos = 0

        # Set up audio
        if path.endswith('.wav'):
            self.audio_file = audio_file = wave.open(path, 'r')
        else:
            # needs my updates in decoder.py
            self.audio_file = audio_file = \
                decoder.open(path, force_mono=mono)

        self.num_channels = 1 if mono else audio_file.getnchannels()

    @property
    def sample_rate(self):
        """Sample rate, such as `44100`. (``int``)"""
        return self.audio_file.getframerate()

    @property
    def duration(self):
        """Duration in seconds. (``int``)"""
        sample_rate = self.audio_file.getframerate()
        if sample_rate:
            return int(self.audio_file.getnframes() / sample_rate)
        else:
            return None

    def set_percentage_pos(self, pos):
        """Seek in the stream."""
        self.pos = pos
        t0 = time()
        x = int(self.audio_file.getnframes() * pos / 100.0)
        self.audio_file.setpos(x)  # needs my workaround in decoder.py
        print("seek took %s" % (time() - t0,))

    def get_percentage_pos(self):
        """
        Get the current position (as an int
        percentage between 0 and 100.
        """
        if self.duration:
            self.pos = min(100, int(self.audio_file.tell() * 100.0 /
                                    self.audio_file.getnframes()))
            self.pos = max(0, self.pos)
        return self.pos

    def readframes(self, n_frames):
        """
        Read data and return exactly n_frames.
        (So if there is 2 bytes per frame, the result data length will
        be 2 * n_frames)
        """
        return self.audio_file.getnframes(n_frames)

    def close(self):
        log.info("Close %s", self)
        self.audio_file.close()
        self.audio_file = None
        # just a safety, should not be needed
        os.system("killall -9 %s" % self._decoder_name)
