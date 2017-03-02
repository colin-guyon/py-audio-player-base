
import sys
import os
from os.path import join, splitext
from time import time, sleep
import shutil
import re
from threading import Thread, RLock
import random

__all__ = ('PlayObjectInterface', 'AudioPlayerInterface',
           'FadeInThread', 'SleepTimerThread')

if sys.version_info >= (3,):
    xrange = range  # xrange is range in python 3


class PrintLogger(object):
    def __init__(self, name):
        self.name = name

    def _log_func(self, *args):
        if len(args) > 1:
            print(self.name + ' ' + (args[0] % args[1:]))
        else:
            print(self.name + ' ' + str(args[0]))

    def _log_exception(self, *args):
        import traceback
        _, ex, tb = sys.exc_info()
        traceback.print_tb(tb)
        print(repr(ex))
        if args:
            self._log_func(args)

    debug = info = warning = error = critical = _log_func
    exception = _log_exception


#: Logger, to be set using :func:`set_logger`
log = PrintLogger(__name__)


def set_logger(logger):
    """Set the logger for this module."""
    global log
    log = logger or PrintLogger(__name__)


try:
    from time import monotonic
except ImportError:
    log.warning("No time.monotonic() function, falling back to time.time()")
    monotonic = time


def is_stream(path):
    """Returns whether the given path is a stream."""
    return "://" in path


class PlayObjectInterface(object):
    """
    Interface class to implement for play objects.

    Following methods must be implemented:
        - :meth:`.open` (open the audio resource given its path)
        - :meth:`.set_percentage_pos` (seek)
        - :meth:`.get_percentage_pos` (get current position)
        - :meth:`.readframes` (read audio data, to be able to play it)
        - :meth:`.close` (resources to release ? ...)
    """
    #: (``int``) Number of channels, should be 1 (if the
    #: player wants mono) or 2
    num_channels = 0
    #: (``int``) Total duration in seconds
    #: (put ``None`` if no duration can be found)
    duration = 0
    #: (``int``) Sample rate, such as 44100
    sample_rate = 0

    def open(self, path, mono=False, sample_rate=44100):
        """
        Open the audio resource (can be a local file or a web URL)
        """
        raise NotImplementedError

    def set_percentage_pos(self, pos):
        """
        Seek in the stream. (pos is a percentage int between 0 and 100)
        """
        raise NotImplementedError

    def get_percentage_pos(self):
        """
        Get the current position (as an int or float
        percentage between 0 and 100.
        """
        raise NotImplementedError

    def readframes(self, n_frames):
        """
        Read data and return exactly n_frames.
        (So if there is 2 bytes per frame, the result data length will
        be 2 * n_frames)
        """
        raise NotImplementedError

    def close(self):
        """
        Close the play objects (some resources to release ? ...)
        """
        raise NotImplementedError


class AudioPlayerInterface(object):
    """
    Interface class to implement for an audio player.

    The following methods must be implemented by real players inheriting
    this interface class:

        - :meth:`._do_play_queue`
        - :meth:`._do_play_pause`
        - :meth:`._do_play_next`
        - :meth:`._do_play_prev`
        - :meth:`._do_stop`
        - :meth:`._do_seek`
        - :meth:`._do_set_volume`

    .. note:: When calling :meth:`play` the playback is done in a new thread
        (:class:`PlayThread`).
    """
    #: (``int``) Size of audio chunks (number of frames), such as 4096.
    audio_chunk_size = 4096

    #: Set this to a valid play object class implementing the
    #: :class:`PlayObjectInterface` interface.
    PlayObjectClass = None

    #: All handled extensions (here it's just an example, but it is
    #: certainly dependant on the :attr:`.PlayObjectClass` you set
    #: on the player)
    handled_extensions = ('.mp3', '.wav')

    #: Whether to wait for the end of the current played track before stopping
    #: playback from a sleep timer.
    sleep_timer_wait_track_end = False

    def __init__(self,
                 default_files_dir='.',
                 removed_files_backup_dir=None,
                 init_volume_level=None, mono=False,
                 notify_progression_interval=None):
        # Using a re-entrant lock instead of simple lock to be able
        # to have nested "with self._lock" without be blocked
        # (the code is a little bit more short and clear)
        self._lock = RLock()

        #: (`str`) the default directory to find audio files
        self.default_files_dir = os.path.abspath(default_files_dir)
        #: (`str`) the backup directory where to place the audio files that are
        #: removed by :meth:`.remove_current`.
        if removed_files_backup_dir:
            self.removed_files_backup_dir = \
                os.path.abspath(removed_files_backup_dir)
        else:
            self.removed_files_backup_dir = None

        #: (`list` of `str`) the current playlist of music (absolute file paths)
        self.queue = []
        # (`int`) current index within the queue
        self._play_index = 0
        #: Instance of a :class:`.PlayObjectClass` for the current played track. 
        self.play_object = None
        #: (`str`) status can be "stopped", "playing", "paused"
        #: (readonly)
        self.status = "stopped"

        # (`str`) the last played music, after a stop
        self._stopped_music = None

        self._play_thread = None
        self._sleep_timer_thread = None
        self._fade_thread = None

        self._seek = None
        self._go_prev = False
        self._go_next = False

        #: (`bool`) Whether the audio must be downmixed to mono (the flag
        #: will be passed to the :meth:`.PlayObjectClass.open` method).
        self.mono = mono

        #: (``int`` or ``float``) Interval at which call
        #: :meth:`._notify_progression` during the playback.
        self.notify_progression_interval = notify_progression_interval

        #: Function to be externally set that would be called each time
        #: :meth:`.set_volume` is called with ``notify=True``
        self.volume_update_handler = None

        #: Function to be externally set that would be called by
        #: the :class:`SleepTimerThread` while its remaining time decreases
        #: (each minute)
        self.sleep_timer_update_handler = None

        self._volume = None
        if init_volume_level is not None:
            self.set_volume(init_volume_level, notify=False)

    @property
    def current(self):
        """
        Current playing music file (`str` or `None`).
        If it's a web stream, the stream url is returned.
        """
        with self._lock:
            if self.status in ("playing", "paused"):
                try:
                    current = self.queue[self._play_index]
                except IndexError:
                    current = None
                else:
                    if isinstance(current, tuple):
                        # for a radio it can be (name, url), so let's return
                        # the real path in all cases
                        current = current[1]
            else:
                current = None
            return current

    @property
    def current_display_name(self):
        """
        Display name of the current playing music file (`str`)
        """
        with self._lock:
            if self.status in ("playing", "paused"):
                try:
                    current = self.queue[self._play_index]
                except IndexError:
                    name = ""
                else:
                    if isinstance(current, tuple):
                        # for a radio it can be (name, url), so let's return
                        # the name to display instead of the url
                        name = current[0]
                    else:
                        name = self.format_music_filename(current)
            else:
                name = ""
            return name

    def format_music_filename(self, path):
        """
        Returns a relative file path without extension, given an absolute path.
        """
        if is_stream(path):
            return path
        # local file
        if path.startswith(self.default_files_dir):
            path = os.path.relpath(path, self.default_files_dir)
        return os.path.splitext(path)[0]

    def play(self, path=None, queue=None, loop=False,
             shuffle=False, fade_in=False):
        """"
        Play a new music file, or folder, or queue from a search ....
        If currently playing, :meth:`stop` is preliminarily called.

        If ``path`` is None, the :attr:`.default_files_dir` is used.

        Calls :meth:`._do_play_queue` in a new thread.
        """
        if self.status != "stopped":
            self.stop()

        path = path or self.default_files_dir
        with self._lock:
            # save the arguments so that we can easily re-play the same
            # thing later
            self._last_play_args = (
                [path], dict(loop=loop, shuffle=shuffle, fade_in=fade_in))

            if queue is not None:
                # we give a list of files to play
                self.queue = queue
                if not self.queue:
                    log.error("empty queue !")
                    return
                elif shuffle:
                    random.shuffle(self.queue)
            elif os.path.isdir(path):
                # Folder of music
                self.queue = self._search('*', root_dir=path)
                if not self.queue:
                    log.error("empty queue !")
                    return
                elif shuffle:
                    random.shuffle(self.queue)

                if self._stopped_music:
                    # try to play the last stopped music first
                    if os.path.isfile(self._stopped_music):
                        self.queue.insert(0, self._stopped_music)
                    else:
                        # TODO: handle case when self._stopped_music
                        #       is a tuple/radio
                        log.warning("Do not insert non existing last "
                                    "music %r at the first position of "
                                    "the queue.", self._stopped_music)
            else:
                # single file (or playlist file)
                self.queue = [path]

            self.status = "playing"
            self._play_index = 0
            self._play_thread = PlayThread()
            self._play_thread.daemon = True
            self._play_thread.start(self)

            self.set_sleep_timer(60)  # TODO: Set the duration configurable

    def _do_play_queue(self):
        """
        Play the audio queue. (Called in a new thread by :meth:`.play`)
        """
        self._do_open_output()

        fade_in = self._last_play_args[1]['fade_in']
        if fade_in:
            self.start_volume_fade_in()

        self._prev_path = None

        open_error_count = 0

        while self.status != "stopped":
            with self._lock:
                if not self.queue:
                    log.info("Empty queue !")
                    break
                try:
                    path = self.queue[self._play_index]
                except IndexError:
                    log.error("Failed to find a track in queue for index %i",
                              self._play_index)
                    self._play_index = 0
                    path = self.queue[0]

            log.info("Will now play %r", path)
            try:
                self.play_object = play_object = self._do_open_path_for_play(path)
            except:
                log.exception("play object creation failed !")
                open_error_count += 1
                if open_error_count > 10:
                    log.error("too much consecutive open failures, stopping player")
                    # Stop the player in a separate thread so that it can
                    # join the play thread
                    Thread(target=self.stop,
                           kwargs=dict(save_current=False)).start()
                    break
                self._play_index += 1  # try with the next track
                sleep(2)  # to avoid an endless loop taking all cpu
                continue

            else:
                # Open worked, let's reset the counter
                open_error_count = 0

            if play_object.duration:
                total_minutes = play_object.duration // 60
                log.info("duration: %d min %d s",
                         total_minutes, play_object.duration % 60)
            else:
                log.info("duration is unknown")

            try:
                self._do_configure_output_for_current_track()

                log.info("Playing: %s" % (path,))

                context = {}  # context dict for the current audio track
                t0 = monotonic()

                if self.notify_progression_interval:
                    # Call the progression handler just before
                    # reading/playing the first audio chunk
                    self._notify_progression(context)

                # read the first chunk of audio data
                data = play_object.readframes(self.audio_chunk_size)

                while data:
                    while self.status == "paused":
                        sleep(0.05)
                    if self._go_prev or self._go_next:
                        break
                    if self.status == "stopped":
                        break

                    # Regularly call _notify_progression to be able to notify
                    # the progression (for example to update a progress bar)
                    # (not if there is a pending seek which will be
                    # processed just after)
                    if self.notify_progression_interval and self._seek is None:
                        t1 = monotonic()
                        if t1 - t0 >= self.notify_progression_interval:
                            t0 = t1
                            self._notify_progression(context)

                    # Write the audio chunk to the audio output.
                    # This method can also be overriden to process the
                    # audio chunk, for example to compute a power spectrum
                    # using FFT
                    self._do_write_data_chunk(data, context)

                    # Detect possible requested seek
                    with self._lock:
                        if self._seek is not None:
                            seek = self._seek
                            self._seek = None
                            try:
                                log.info("seek detected: %r", seek)
                                play_object.set_percentage_pos(seek)
                            except:
                                log.exception("seek exception")

                    # Read next chunk of data from music
                    data = play_object.readframes(self.audio_chunk_size)

            except StopIteration:
                # play_object.readframes has certainly raised this because it
                # reached the end of the playback
                pass
            except Exception:
                log.exception("exception during play")
            finally:
                play_object.close()
                self.play_object = None

            # Handle previous/next commands or normal end of current music file

            if self._go_next or self._go_prev:
                # In this case the play index is already shifted to
                # go next/prev.
                # Just reset the flags.
                self._go_prev = self._go_next = False
            else:
                # Continue the playlist
                self._play_index += 1

            self._prev_path = path

        log.debug("end of queue")
        self._do_close_output()
        log.debug("Status after end of queue: %r", self.status)

    def _do_open_output(self):
        """
        Open the output audio interface, before playing the track queue.
        """
        # self.output = ...

    def _do_configure_output_for_current_track(self):
        """
        Configure the alsa output for the track that will be played.
        """
        # play_object = self.play_object
        # output = self.output
        # output.set...

    def _do_close_output(self):
        """
        Close the alsa output audio interface.
        """
        # self.output.close()
        # self.output = None

    def _do_open_path_for_play(self, path):
        """
        Open a file path or a stream for a play
        (called by :meth:`._do_play_queue`)

        :param path: File path or web stream to play
        :return: An object implementing the :class:`PlayObjectInterface`
            interface.
        """
        play_object = self.PlayObjectClass()
        play_object.open(path, mono=self.mono)
        return play_object

    def _do_write_data_chunk(self, data, context):
        """
        Directly writes given data to the alsa output.
        (called by :meth:`._do_play_queue`)

        You can also do whatever you want with the data that was just
        written to the audio output, such as an FFT analysis.

        :param context: A play context ``dict`` that is specific
            to the current played track.
        """
        # self.output.write(data)

    def _notify_progression(self, context):
        """
        Handler regularly called during the playback.
        (see :attr:`.notify_progression_interval`), that can be
        implemented to update a progress bar for example.
        You must not do long actions in this function, otherwise
        the playback can be degraded (if you need to do heavy actions,
        consider using a separate thread instead of using this method
        which is called in the playback thread).

        :param context: A play context ``dict`` that is specific
            to the current played track.
        """
        pass

    def stop(self, save_current=True, fade_out=False):
        """ stop the music if any """
        log.debug('Stop player')
        with self._lock:
            fade_thread = self._fade_thread
            if (fade_out and fade_thread and fade_thread.running
                    and isinstance(fade_thread, FadeOutThread)):
                log.debug("A fade out was requested but one is already "
                          "ongoing... Let's just stop now.")
                fade_out = False
            # Anyway Stop the current fade in or out
            self.stop_volume_fade()
            if self.status != "stopped":
                if fade_out:
                    if self.volume == 0:
                        log.warning("Volume is already equal to 0, "
                                    "don't need to fade out.")
                    else:
                        log.info("Start fade out and return: stop will be done "
                                 "by the fade out thread")
                        self.start_volume_fade_out()
                        return

                self._stopped_music = self.current if save_current else None
                self._do_stop()
                self.status = "stopped"

            self.set_sleep_timer(None)

        if self._play_thread:
            # Status is stopped, wait to be sure the current play
            # thread dies (without acquiring self._lock since it may
            # certainly be done in the play thread, so it would end
            # to a deadlock).
            log.debug("Join() play thread")
            self._play_thread.join()
            log.debug("done")
            self._play_thread = None

    def _do_stop(self):
        """ Stop the current playing track if any. Called by :meth:`.stop`. """
        self._seek = None
        self._go_prev = False
        self._go_next = False

    def play_pause(self, shuffle=False):
        """ play or pause the music """
        log.debug('Play or pause')
        with self._lock:
            if self.status == "stopped":
                self.play(shuffle=shuffle)
            else:
                self._do_play_pause()
                self.status = ("paused" if self.status == "playing"
                               else "playing")

    def _do_play_pause(self):
        """
        Play or pause, called by :meth:`.play_pause`.
        To be implemented by real players if more than having the
        :data:`.status` set to ``"paused"`` is needed.
        """
        pass

    def play_next(self):
        """ Go to the next song """
        log.debug('Play next')
        with self._lock:
            if self.status != "stopped":
                self._play_index += 1
                return self._do_play_next()
            else:
                log.error("Cannot play next song: status=%r", self.status)
                return False

    def _do_play_next(self):
        """
        Go to the next song. Called by :meth:`.play_next`.
        Must return ``True`` if the action succeeded, else ``False``.
        """
        self._go_next = True
        return True

    def play_prev(self):
        """ Go to the previous song """
        log.debug('Play prev')
        with self._lock:
            if self.status != "stopped":
                self._play_index = (self._play_index - 1) % len(self.queue)
                return self._do_play_prev()
            else:
                log.error("Cannot play previous song: status=%r", self.status)
                return False

    def _do_play_prev(self):
        """
        Go to the next song. Called by :meth:`.play_next`.
        Must return ``True`` if the action succeeded, else ``False``.
        """
        self._go_prev = True
        return True

    def seek(self, val):
        """
        Seek to the given value.

        :param val: percentage (``int``) between 0 and 100.
        """
        with self._lock:
            if self.status != "stopped":
                if is_stream(self.current):
                    log.error("Cannot seek in a stream")
                else:
                    val = int(val)
                    assert 0 <= val <= 100
                    self._do_seek(val)
            else:
                log.error("Cannot seek: status=%r", self.status)

    def _do_seek(self, val):
        """ Do a seek. Called by :meth:`.seek`. """
        # Store the seek value, and the real seek will be done in the
        # _do_play_queue
        self._seek = val

    def set_volume(self, volume, notify=True):
        """
        Set the audio volume.

        :param volume: ``int`` between 0 and 100.
        :param notify: ``bool``, whether to call
            :data:`.volume_update_handler` if it is set to notify the
            volume change.
        """
        with self._lock:
            self._do_set_volume(volume)
            self._volume = volume
            if notify and self.volume_update_handler:
                self.volume_update_handler(volume)

    def _do_set_volume(self, volume):
        """ Set the audio volume. (Called by :meth:`.set_volume`) """
        raise NotImplementedError

    @property
    def volume(self):
        """ Current volume. """
        return self._volume

    def start_volume_fade_in(self):
        """ Start a thread to fade-in the volume. The volume is
        preliminary set to 0.  """
        self.set_volume(0)
        # Normally if a FadeThread was running it has been stopped in the
        # last call of stop() or play(). So directly start a new one:
        self._fade_thread = t = \
            FadeInThread(lambda: self.volume, self.set_volume)
        t.start()

    def start_volume_fade_out(self):
        """ Start a thread to fade-out the volume. :meth:`.stop` will
        be called that the end of the fade.  """
        # Normally if a FadeThread was running it has been stopped in the
        # last call of stop() or play(). So directly start a new one:
        self._fade_thread = t = \
            FadeOutThread(lambda: self.volume, self.set_volume, self.stop)
        t.start()

    def stop_volume_fade(self):
        """ Stop the thread that fades the volume (if running) """
        with self._lock:
            if self._fade_thread:
                log.debug("Set running=False on %s", self._fade_thread)
                self._fade_thread.running = False
                self._fade_thread = None

    def remove_current(self, backup=True):
        """ Remove the current playing file.

        :param backup: whether to move the file to the
            :attr:.`removed_files_backup_dir` folder or to simply remove it.
        :type backup: bool
        """
        with self._lock:
            current = self.current
            if not current:
                log.error("Cannot remove, nothing being played !")
                return False
            elif not os.path.isfile(current):
                log.error("Cannot remove %r which is not a file !", current)
                return False

            try:
                if backup:
                    if (not self.removed_files_backup_dir or
                            not os.path.isdir(self.removed_files_backup_dir)):
                        raise ValueError("Cannot do a backup: invalid "
                                         "removed_files_backup_dir. "
                                         "Do not delete the file.")

                    if current.startswith(self.default_files_dir):
                        rel_path = os.path.relpath(current,
                                                   self.default_files_dir)
                    else:
                        rel_path = current
                        if current.startswith('/') or current.startswith('\\'):
                            rel_path = current[1:]
                    target_file = join(self.removed_files_backup_dir, rel_path)
                    target_folder = os.path.dirname(target_file)
                    if not os.path.exists(target_folder):
                        os.makedirs(target_folder)
                    shutil.move(current, target_file)
                else:
                    os.remove(current)
            except OSError as e:
                log.error("Remove current music %r, failed: %r", current, e)
                raise
            else:
                log.info("Successful remove of %r", current)
                del self.queue[self._play_index]
                if self.queue:
                    # decrement the play index so that play_next will
                    # play the next song
                    self._play_index -= 1
                    self.play_next()

                self._on_track_removed(current)

        if not self.queue:
            log.info("No more item in the playlist !")
            # Call stop() outside 'with self._lock' because
            # it does a join on the current play thread that
            # may need to acquire the lock.
            self.stop()

        return True

    def _on_track_removed(self, path):
        """
        Handler called by :meth:`remove_current`, that could be implemented
        to remove the given path from a database for example.
        """
        pass

    def search_and_play(self, pattern, shuffle=True):
        """
        Search musics given a string pattern (regex) and play results if any
        """
        pattern = pattern.strip()
        log.info("search_and_play: %r" % pattern)
        queue = self._search(pattern)
        if not queue:
            log.warning("No results for %r pattern! Don't play", pattern)
        else:
            if shuffle and pattern == '#recent':
                # play all recent files in shuffle could be strange ?
                shuffle = False
            self.play(queue=queue, shuffle=shuffle)

    def _search(self, pattern, root_dir=None):
        """
        Search musics given a string pattern (regex).

        :returns: The list of matching files / URLs
        """
        t0 = time()
        queue = ()

        if root_dir is None:
            root_dir = self.default_files_dir

        handled_extensions = self.handled_extensions

        if not pattern or pattern == '*':
            log.info("Search all files in %r", root_dir)
            queue = [join(root, file_name)
                     for root, _, file_names in os.walk(root_dir)
                     for file_name in file_names
                     if splitext(file_name)[1] in handled_extensions]

        elif is_stream(pattern):
            # play a web stream
            queue = [pattern]

        elif pattern.startswith('#'):
            # Special query with keyword and optional options
            key, sep, options = pattern.partition(':')

            if key == "#recent":
                # special '#recent' query allowing to play all files ordered by
                # modification date (descending)
                queue = [join(root, file_name)
                         for root, _, file_names
                         in os.walk(self.default_files_dir)
                         for file_name in file_names
                         if splitext(file_name)[1] in handled_extensions]
                queue.sort(key=os.path.getmtime, reverse=True)
            else:
                log.error("Unknown special '#' query %r", pattern)

            if queue and options.isdigit():
                # reduce the list to a given amount of files.
                # for example: '#recent:10' will play the 10 most recent files
                limit = int(options)
                queue = queue[:limit]
                log.info("Queue reduced to its %d first elements", limit)

        else:
            # Normal search with given pattern on local files
            match = re.match
            queue = []
            add = queue.append

            regexp = re.compile("^.*" + pattern, re.IGNORECASE)

            for root, _, file_names in os.walk(self.default_files_dir):
                for file_name in file_names:
                    if splitext(file_name)[1] not in handled_extensions:
                        continue
                    full_path = join(root, file_name)
                    if match(regexp, full_path):
                        add(full_path)

        log.info("Found %s results in %ss" % (len(queue), time() - t0))
        return queue

    def set_sleep_timer(self, duration):
        """
        Start a new sleep timer with given duration in minutes.
        The duration can either be a ``str`` or an ``int``.
        A ``None`` duration means stop the current sleep timer if any.
        """
        with self._lock:
            if self._sleep_timer_thread is not None:
                self._sleep_timer_thread.running = None
                self._sleep_timer_thread = None
            if duration is not None:
                duration = int(duration)
            if duration:
                self._sleep_timer_thread = t = SleepTimerThread(self)
                t.start(duration)

    @property
    def sleep_timer_thread(self):
        """The current :class:`SleepTimerThread` or ``None``."""
        return self._sleep_timer_thread


class PlayThread(Thread):
    """
    Instantiated and started by :meth:`AudioPlayerInterface.play`
    """
    def __init__(self):
        super(PlayThread, self).__init__()
        self.player = None

    def start(self, player):
        self.player = player
        Thread.start(self)

    def run(self):
        try:
            self.player._do_play_queue()
        except:
            log.exception("_do_play_queue exception !")
            raise


class FadeInThread(Thread):
    """
    Thread that slowly increases the volume
    """
    def __init__(self, get_volume_func, set_volume_func, max_volume=85):
        Thread.__init__(self)
        self.running = False
        self.get_volume = get_volume_func
        self.set_volume = set_volume_func
        self.daemon = True
        self.max_volume = max_volume

    def start(self):
        self.running = True
        Thread.start(self)

    def run(self):
        max_vol = self.max_volume
        get_volume, set_volume = self.get_volume, self.set_volume
        for vol in xrange(5, max_vol, 4):
            if get_volume() < vol:
                set_volume(vol)
            if not self.running:
                return
            sleep(3.5)
            if not self.running:
                return
        if get_volume() < max_vol:
            set_volume(max_vol)


class FadeOutThread(Thread):
    """
    Thread that slowly decreases the volume
    """
    def __init__(self, get_volume_func, set_volume_func, stop_func):
        Thread.__init__(self)
        self.running = False
        self.get_volume = get_volume_func
        self.set_volume = set_volume_func
        self.stop_player = stop_func
        self.daemon = True

    def start(self):
        self.running = True
        Thread.start(self)

    def run(self):
        log.debug("FadeOutThread run")
        get_volume, set_volume = self.get_volume, self.set_volume
        orig_volume = get_volume()
        for vol in xrange(orig_volume, 0, -4):
            if get_volume() > vol:
                log.debug("FadeOutThread : set_volume(%s)", vol)
                set_volume(vol)
            if not self.running:
                return
            sleep(0.5)
            if not self.running:
                return
        if get_volume() > 0:
            log.debug("FadeOutThread : set_volume(0)")
            set_volume(0)
        log.debug("FadeOutThread : stop player")
        self.stop_player(fade_out=False)
        # Restore the initial volume
        set_volume(orig_volume)


class SleepTimerThread(Thread):
    """Sleep timer (not yet customizable)"""
    def __init__(self, player):
        Thread.__init__(self)
        self.running = False
        self.player = player
        self.daemon = True
        # Remaining duration in minutes
        self.remaining = None

    def start(self, duration):
        self.running = True
        self.remaining = duration
        Thread.start(self)

    def run(self):
        log.info("%s started", self)
        player = self.player

        # Call the player sleep timer handler if any
        if player.sleep_timer_update_handler is not None:
            player.sleep_timer_update_handler(self.remaining)

        nb_iterations = self.remaining
        for i in xrange(nb_iterations):
            sleep(60)
            self.remaining -= 1
            if not self.running:
                log.info("leaving aborted %s", self)
                return

            # Call the player sleep timer handler if any
            if player.sleep_timer_update_handler is not None:
                player.sleep_timer_update_handler(self.remaining)

            if i > 0.75 * nb_iterations:
                player.set_volume(max(0, self.player.volume - 1))

        play_obj = player.play_object
        play_obj_duration = play_obj.duration
        if player.sleep_timer_wait_track_end and play_obj_duration:
            log.info("Sleep timer: waiting current track end")
            while self.running and player.play_object is play_obj:
                sleep(1)
                track_remaining_time = (play_obj_duration -
                                        (play_obj.get_percentage_pos() / 100.0
                                         * play_obj_duration))
                if track_remaining_time < 10:
                    log.info("Sleep timer: track end will be in %s seconds, "
                             "stopping now", track_remaining_time)
                    break

        if not self.running:
            log.info("leaving aborted %s", self)
            return

        log.info("Sleep timer reached ! %s", self)
        player.stop(fade_out=True)
