
from time import sleep
from audio_player.alsa import AlsaAudioPlayer
from audio_player.pyav import PyAVPlayObject


class MyAudioPlayer(AlsaAudioPlayer):

    PlayObjectClass = PyAVPlayObject

    def _notify_progression(self, context):
        """Regularly called, for example to update a progress bar"""
        current_percent_pos = self.play_object.get_percentage_pos()
        total_duration_seconds = self.play_object.duration
        current_pos_seconds = int(current_percent_pos / 100. * total_duration_seconds)
        print(" {:.1f}% (-{:d}s)".\
              format(current_percent_pos,
                     total_duration_seconds - current_pos_seconds))

PLAYER = MyAudioPlayer(default_audio_files_dir='/run/media/colin/Data/Music',
                       root_files_dir='/run/media/colin/Data/',
                       mono=False, init_volume=None,
                       notify_progression_interval=1.0)

PLAYER.play(random=True)

try:
    while PLAYER.status != 'stopped':
        sleep(1)
        r = input()
        if r == 's':
            PLAYER.seek(95)
        elif r == 'n':
            PLAYER.play_next()
        elif r == 'p':
            PLAYER.play_prev()
        elif r == 'r':
            PLAYER.seek(0)
except KeyboardInterrupt:
    PLAYER.stop()
