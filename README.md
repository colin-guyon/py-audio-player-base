# py-audio-player

Python package containing various audio player classes.


  - ``AudioPlayerInterface`` : Interface class for an audio player.

      Main methods :

          - ``play`` : Play a single file or a whole folder, or a list of files.
          - ``search_and_play`` : Search among a default search dir and play the matching results.
          - ``play_pause`` : Pause if currently playing, else start a new play.
          - ``play_next``
          - ``play_prev``
          - ``stop``
          - ``seek``
          - ``set_volume``
          - ``start_volume_fade_in``
          - ``stop_volume_fade``
          - ``remove_current`` :  Remove the currently played track.

      Main readonly attributes and properties :

          - ``status`` : Current play status (`"playing"`, `"paused"`, `"stopped"`)
          - ``volume`` : Current volume level (between 0 and 100)
          - ``current`` : Current playing track path
          - ``current_display_name`` : Current playing track pretty name

      Main hooks / methods to implement in real player classes :

          - ``_do_open_path_for_play`` : Open an audio track so that it can be played.
                                         An instance of a class implementing ``PlayObjectInterface``
                                         must be returned, after having called its ``open`` method.
                                         Overriding this method you can also notify the new audio
                                         track name that will be played for example ...

          - ``_do_open_output`` : Open the audio output that will be used to hear the sound
                                  (called before the play queue is started).

          - ``_do_close_output`` : Close the audio output after a playback
                                   (called when the playback is finished, after a manual
                                   stop or the end of a playlist).
                                   The ``_on_playback_stopped`` method is called after
                                   ``_do_close_output`` and can be implemented for things that
                                   are not related to the audio output.

          - ``_do_write_data_chunk`` : Write an audio data chunk to the audio output.
                                       You can also process the data chunk in this method for
                                       example to display a power spectrum using FFT (but do not
                                       do too long actions, otherwise the playback would be degraded).

          - ``_notify_progression`` : Override this method that is regularly called to update
                                      a progress bar for example (not for heavy actions because
                                      called in the playback thread).

          - ``_on_track_removed`` : Called after the ``remove_current`` method has been called,
                                    and has effectively removed a file, for example to also remove
                                    the file path from a database.

  - ``PlayObjectInterface`` : Interface class for a play object used by a ``AudioPlayerInterface``.
  - ``SubprocessDecoderPlayObject`` : Play object class implenting ``PlayObjectInterface`` and
                                      using `decoder.py` for decoding
                                      (it uses lame, avconv or ffmpeg in subprocess).
  - ``PyAVPlayObject`` : Play object class implenting ``PlayObjectInterface``
                         and using `PyAV` (that is to say the ffmpeg or libav lib).
                         Compared to ``SubprocessDecoderPlayObject`` it allows to
                         have a much faster seek, and faster track previous/next operations.
  - ``AlsaAudioPlayer`` : A player class implementing ``AudioPlayerInterface``
                          that uses ALSA for audio output and volume control.
  - ``SubprocessAudioPlayer`` : A player class implementing ``AudioPlayerInterface``
                                that uses a player in a sub process, such as `mplayer`.
                                It does not allow to retrieve audio data chunks and process
                                them.

The most simple usage example
-----------------------------

.. sourcecode:: python

    from time import sleep
    from audio_player.alsa import AlsaAudioPlayer
    from audio_player.pyav import PyAVPlayObject

    class MyAudioPlayer(AlsaAudioPlayer):
        # A real PlayObjectClass implementing ``PlayObjectInterface``
        PlayObjectClass = PyAVPlayObject

    PLAYER = MyAudioPlayer()
    PLAYER.play()

    try:
        while PLAYER.status != 'stopped':
            sleep(1)
    except KeyboardInterrupt:
        PLAYER.stop()


Other simple usage example
--------------------------

.. sourcecode:: python

    from audio_player.alsa import AlsaAudioPlayer
    from audio_player.pyav import PyAVPlayObject

    class MyAudioPlayer(AlsaAudioPlayer):
        # ALSA mixer name for volume control
        mixer_name = 'Master'
        # A real PlayObjectClass implementing ``PlayObjectInterface``
        PlayObjectClass = PyAVPlayObject

        def _do_write_data_chunk(self, data, context):
            # Write the data to the ALSA audio output,
            # calling the parent class method
            super(MyAudioPlayer, self)._do_write_data_chunk(data, context)
            # Call some custom function that analyzes the data
            process_fft_and_update_power_spectrum(data, context)

        def _notify_progression(self, context):
            # Regularly called, for example to update a progress bar
            current_percent_pos = self.play_object.get_percentage_pos()
            total_duration_seconds = self.play_object.duration
            current_pos_seconds = int(current_percent_pos / 100. * total_duration_seconds)
            print("Current play pos is {:.1f}% (-{:d}s)".\
                  format(current_percent_pos,
                         total_duration_seconds - current_pos_seconds))

    root_dir = '/my/usb/key/'
    audio_dir = os.path.join(root_dir, 'music')
    audio_trash_dir = os.path.join(root_dir, 'music_trash')

    player = MyAudioPlayer(default_files_dir=audio_dir,
                           removed_files_backup_dir=audio_trash_dir)

    player.play_pause()
    # ...
    player.play_next()
    # ...
    player.remove_current()
    # ...
    player.search_and_play("some pattern")
    # ...
    player.stop()


Notes when using Python 3
-------------------------

When using python 3, to use ``PyAVPlayObject`` with non-ascii audio file names
consider applying the small patch attached in https://github.com/mikeboers/PyAV/issues/82
(`av.open does not accept unicode name in Python 3`) to PyAV, before manually installing it.
See http://mikeboers.github.io/PyAV/installation.html.

TODO
----

  - Write unit tests

Links
-----

Thanks to the following projects that are used under the hood:

  - pyalsaaudio: http://larsimmisch.github.io/pyalsaaudio/
  - PyAV : http://mikeboers.github.io/PyAV/
  - FFmpeg : https://ffmpeg.org/
  - libav : https://libav.org/
  - decoder.py : https://pypi.python.org/pypi/decoder.py/

... and all those that I forget to mention !
