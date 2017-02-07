"""
ALSA based audio player (for playing and volume control, using the
python alsaaudio package).

To be usable, a :attr:`PlayObjectClass` must be set
on :class:`AlsaAudioPlayer`.
"""
import alsaaudio as aa

from .interface import log, AudioPlayerInterface

__all__ = ('AlsaAudioPlayer',)


log.debug("Available ALSA mixers : %s", aa.mixers())


class AlsaAudioPlayer(AudioPlayerInterface):
    """
    Player playing to a Alsa audio output, using the py-alsaaudio package.

    To be usable, the :attr:`.PlayObjectClass` must be set.
    """
    #: Name of the ALSA mixer to use for volume control.
    #: (You can list the available mixers on you system using
    #: pyalsaaudio mixers() method)
    mixer_name = 'Master'

    # Alsa mixer for volume control
    mixer = aa.Mixer(mixer_name)

    def __init__(self, *args, **kwargs):
        log.debug("mixer is %r", self.mixer.mixer())
        super(AlsaAudioPlayer, self).__init__(*args, **kwargs)

    def _do_open_output(self):
        """
        Open the alsa output audio interface, before playing the track queue.
        """
        log.debug("Open alsa audio output")
        self.output = aa.PCM(aa.PCM_PLAYBACK, aa.PCM_NORMAL)

    def _do_configure_output_for_current_track(self):
        """
        Configure the alsa output for the track that will be played.
        """
        play_object = self.play_object
        log.info("Configure ALSA output : %s %s %s" % (play_object.num_channels,
                                                       play_object.sample_rate,
                                                       self.audio_chunk_size))
        output = self.output
        output.setchannels(play_object.num_channels)
        output.setrate(play_object.sample_rate)
        output.setformat(aa.PCM_FORMAT_S16_LE)
        output.setperiodsize(self.audio_chunk_size)

    def _do_close_output(self):
        """
        Close the alsa output audio interface.
        """
        log.debug("closing alsa audio output")
        self.output.close()
        self.output = None

    def _do_write_data_chunk(self, data, context):
        """
        Directly writes given data to the alsa output.
        (called by :meth:`._do_play_queue`)

        :param context: A play context ``dict`` that is specific
            to the current played track.
        """
        self.output.write(data)

    def _do_set_volume(self, volume):
        """
        Set the audio volume.
        (Called by :meth:`set_volume`)

        :param volume: ``int`` between 0 and 100.
        """
        self.mixer.setvolume(volume)
