"""
Audio player relying on an external to both decode and play
audio files, such as mplayer.
"""

import os
import re
from time import sleep
import subprocess

from .interface import log, AudioPlayerInterface, FadeInThread

# NonBlockingStreamReader permits to parse a subprocess stdout
# without being blocked
from .nbstreamreader import NonBlockingStreamReader

__all__ = ('SubprocessAudioPlayer',)



class SubprocessAudioPlayer(AudioPlayerInterface):
    """ Player using another program in a subprocess to decode and play
    audio, such as mplayer. """

    # player program to use
    player = "mplayer"
    player_default_options = ['-slave', '-quiet']

    _percent_pos_regex = re.compile("ANS_PERCENT_POSITION=(\d+)")

    _quit_cmd = 'quit\n'
    _pause_cmd = 'pause\n'

    def __init__(self):
        super(SubprocessAudioPlayer, self).__init__()
        # current subprocess.Popen object for the music being played
        self.current_process = None

    def _do_play_queue(self):
        """ called by :meth:`.play` """
        options = []
        options.extend(self.player_default_options)
        # if loop:
        #     options.append("-loop 0")
        # -> loop is handled outside of the player now
        # if random:
        #     options.append("-shuffle")
        # -> shuffle is handled outside of the player now
        # if playlist:
        #     options.append("-playlist")

        bluetooth_manager = wake_pi_up.bluetooth_manager

        fade_in = self._last_play_args[1]['fade_in']
        if fade_in:
            self.start_volume_fade_in()

        while self.status != "stopped":
            with self._lock:
                try:
                    path = self.queue[self._play_index]
                except IndexError:
                    log.error("Failed to find a music in queue for index %i",
                              self._play_index)
                    self._play_index = 0

                cmd = "%s %s %r" % (self.player, " ".join(options), path)
                log.info("Popen %r", cmd)
                cmd_list = [self.player]
                cmd_list.extend(options)
                cmd_list.append(path)
                self.current_process = p = subprocess.Popen(cmd_list,
                                                            bufsize=0,
                                                            stdin=subprocess.PIPE,
                                                            stdout=subprocess.PIPE,
                                                            preexec_fn=os.setsid)

                self.status = "playing"
                log.info("Play of %r is launched !", path)
                self.sleep_tag = None  # we will set it to True/False later
                if bluetooth_manager.connected:
                    # send the music file name to the phone
                    bluetooth_manager.send(("current_music:%s" % self.format_music_filename(path),
                                            "current_music_index:%i/%i" % (self._play_index + 1, len(self.queue)),
                                            "music_pos:0"))

            # wrap p.stdout with a NonBlockingStreamReader object:
            nbsr = NonBlockingStreamReader(p.stdout)

            position = None
            while self.current_process is p:
                for i in xrange(20):
                    if self.current_process is not p:  # None if stopped
                        break
                    sleep(0.02)
                try:
                    # Read all current available stdout from the subprocess
                    while True:
                        output = nbsr.readline(0.01)
                        if not output:
                            break
                    # Now we can ask the playing position and send it to the phone
                    # so that it can update its progress bar
                    p.stdin.write("pausing_keep_force get_percent_pos\n")
                    out = nbsr.readline(0.05)
                    if out:
                        match = self._percent_pos_regex.search(out)
                        if match:
                            pos = int(match.group(1))
                            if bluetooth_manager.connected and pos != position:
                                position = pos
                                bluetooth_manager.send("music_pos:%s" % position)
                except Exception as poll_exc:
                    log.error(poll_exc)
                    break

            rc = p.wait()
            log.info("Play of %r finished (%r) ! ", path, rc)

            with self._lock:
                if self.current_process is None:
                    log.info("stop detected ! (self.status = %r)", self.status)  # status should be "stopped" here
                    break
                elif self.current_process is not p:
                    # maybe stopped, or new play launched
                    log.info("subrocess was changed, leaving !")
                    return
                else:
                    # handle previous/next commands or normal end of current music file
                    op = getattr(p, "op", None)
                    if op is None:
                        # automatically continue the playlist
                        self._play_index += 1
                    # else op == "prev" or op == "next" and the play index is already shifted

        # play of queue is finished
        with self._lock:
            self.status = "stopped"

    def _do_stop(self):
        """ Stop the music, called by :meth:`.stop` """
        # Send the signal to all the process groups
        try:
            self.current_process.stdin.write(self._quit_cmd)
        except Exception as e:
            log.error(e)
        try:
            os.killpg(self.current_process.pid, signal.SIGTERM)
        except Exception as e:
            log.error(e)
        finally:
            # just a safety, should not be needed
            os.system("killall -9 %s" % self.player)

        self.current_process = None

    def _do_play_pause(self):
        """ Play or pause the music, called by :meth:`.play_pause` """
        try:
            self.current_process.stdin.write(self._pause_cmd)
        except Exception as e:
            log.error("Error trying to play/pause: %r", e)
            # self.play(*self._last_play_args[0], **self._last_play_args[1])

    def _do_play_next(self):
        """
        Go to the next song, called by :meth:`.play_next`.
        Must return ``True`` if the action succeeded, else ``False``.
        """
        self.current_process.op = "next"
        try:
            # self.current_process.stdin.write("pt_step 1\n")
            self.current_process.stdin.write(self._quit_cmd)
            os.system("killall -9 %s" % self.player)
        except Exception as e:
            log.error("Error trying to play next song: %r", e)
            return False
        else:
            return True

    def _do_play_prev(self):
        """
        Go to the previous song, called by :meth:`.play_prev`.
        Must return ``True`` if the action succeeded, else ``False``.
        """
        self.current_process.op = "prev"
        try:
            # self.current_process.stdin.write("pt_step -1\n")
            self.current_process.stdin.write(self._quit_cmd)
            os.system("killall -9 %s" % self.player)
        except Exception as e:
            log.error("Error trying to play previous song: %r", e)
            return False
        else:
            return True

    def _do_seek(self, val):
        """Seek to the given value, called by :meth:`.seek`"""
        try:
            self.current_process.stdin.write("seek %s 1\n" % val)
        except Exception as e:
            log.error("Error trying to seek: %r", e)

