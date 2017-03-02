# -*- coding: utf-8 -*-
"""
Play object class using PyAV (thus ffmpeg or libav).
"""

from time import time

# PyAV : direct usage of ffmpeg/libav without subprocess
import av

from .interface import log, PlayObjectInterface

__all__ = ('PyAVPlayObject',)


class PyAVPlayObject(PlayObjectInterface):
    data = b''
    last_frame = None
    decode_iter = None
    pos = None
    stream = None

    def open(self, path, mono=False, sample_rate=44100):
        """Open the audio resource."""
        container = av.open(path, options={'usetoc': '1',
                                           # Timeouts of I/O operations in µs and ms
                                           'timeout': '5000000', 'listen_timeout': '5000'})
        # 'usetoc' is set to enable fast seek (see also
        # ffmpeg commit c43bd08 for a 'fastseek' option)
        log.debug(container)
        stream = self.stream = \
            next(s for s in container.streams if s.type == 'audio')
        log.debug(stream)

        resampler = av.AudioResampler(
            format=av.AudioFormat('s16').packed,
            layout='mono' if mono else stream.layout,
            rate=sample_rate)

        def decode_iter():
            """Genrator reading and decoding the audio stream."""
            for packet in container.demux(stream):
                for frame in packet.decode():
                    frame = resampler.resample(frame)
                    yield frame

        self.decode_iter = decode_iter()
        self.pos = 0

        # Duration in seconds
        if stream.duration:
            self.duration = int(stream.duration * stream.time_base)
        else:
            # It is certainly a web file
            log.info("No duration")
            self.duration = None

        self.num_channels = 1 if mono else stream.channels
        self.sample_rate = resampler.rate

    def set_percentage_pos(self, pos):
        self.pos = pos
        stream = self.stream
        time_pos = int(pos / 100.0 * stream.duration) + stream.start_time
        t0 = time()
        stream.seek(time_pos, mode='time')
        log.debug("stream.seek took %s", time() - t0)

    def get_percentage_pos(self):
        if self.duration and self.stream is not None:
            last_pts = self.last_frame.pts if self.last_frame is not None else 0
            self.pos = max(0, min(100, (last_pts * float(self.stream.time_base)
                                        / self.duration * 100)))
        return self.pos

    def readframes(self, n_frames):
        """
        Read data and return exactly n_frames.
        (So if there is 2 bytes per frame, the result data length will
        be 2 * n_frames)

        :raises: ``StopIteration`` when play is finished.
        """
        n_bytes = 2 * n_frames  # 2 bytes per frame
        data = self.data
        while len(data) < n_bytes:
            frame = next(self.decode_iter)
            self.last_frame = frame
            data += frame.planes[0].to_bytes()
            if not data:
                break
        data, remaining_data = data[:n_bytes], data[n_bytes:]
        # save the remaining data for the next call
        self.data = remaining_data
        return data

    def close(self):
        log.info("Close %s", self)
        self.data = None
        self.decode_iter = None
        self.last_frame = None
        self.stream = None
