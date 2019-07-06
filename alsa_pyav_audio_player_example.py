"""
Simple player example.

For eg.:
> PYTHONPATH=src python alsa_pyav_audio_player_example.py http://live.radiogrenouille.com/live
"""

import sys
from time import sleep
from audio_player_base.alsa import AlsaAudioPlayer
from audio_player_base.pyav import PyAVPlayObject


class MyAudioPlayer(AlsaAudioPlayer):
    PlayObjectClass = PyAVPlayObject

    def _notify_progression(self, context):
        """Regularly called, for example to update a progress bar"""
        current_percent_pos = self.play_object.get_percentage_pos()
        total_duration_seconds = self.play_object.duration
        if not total_duration_seconds:
            return
        current_pos_seconds = int(current_percent_pos / 100. * total_duration_seconds)
        print(" {:.1f}% (-{:d}s)".
              format(current_percent_pos,
                     total_duration_seconds - current_pos_seconds))

if __name__ == '__main__':
    player = MyAudioPlayer(default_files_dir='.',
                           removed_files_backup_dir='./music_trash',
                           notify_progression_interval=1.0)
    player.play(queue=sys.argv[1:], shuffle=True)

    cmd2action = {
        ' ': player.play_pause,
        's': lambda: player.seek(95),
        'n': player.play_next,
        'p': player.play_prev,
        'r': lambda: player.seek(0),
        'd': lambda: player.remove_current(backup=True),
        'q': player.stop,
    }

    try:
        while player.status != 'stopped':
            sleep(1)
            r = input()
            action = cmd2action.get(r)
            if action is not None:
                action()
            else:
                # Maybe a search query like '#recent' ?
                player.search_and_play(r)
    except KeyboardInterrupt:
        player.stop()
