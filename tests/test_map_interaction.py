import asyncio
import json
import unittest
from io import StringIO
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, Mock, patch

from seed_map_view import follow, main


class Event:
    def __iadd__(self, callback):
        return self


class MapInteractionTests(unittest.TestCase):
    def test_dimension_change_recenters_even_when_player_coordinates_do_not_change(self):
        config = dict(seed='1', edition='bedrock', version='26.60–26.65',
                      dimension='Overworld', x=None, z=None, follow=True)
        window = Mock(uid='map')
        window.events.shown.is_set.return_value = True
        closed = Mock()
        closed.is_set.return_value = False
        stop = Mock()
        stop.is_set.side_effect = [False] * 6 + [True]
        reader = Mock(read=AsyncMock(return_value=(-566, 658)))
        states = [dict(ready=True, dimension='overworld')] * 3 + [dict(ready=True, dimension='nether')] * 3
        with (TemporaryDirectory() as directory,
              patch('seed_map_view.DATA_DIR', Path(directory)),
              patch('seed_map_view.MinecraftWindow') as game,
              patch('seed_map_view.CoordinateReader', return_value=reader),
              patch('seed_map_view.mss.MSS'),
              patch('seed_map_view.client_area', return_value=dict(left=0, top=0, width=1280, height=720)),
              patch('seed_map_view.position_overlay'),
              patch('seed_map_view.map_world', return_value=dict(seed='1', platform='bedrock_26_60')),
              patch('seed_map_view.map_view_state', side_effect=states),
              patch('seed_map_view.execute', side_effect=lambda _, script: None if 'delete window.bobberDestination' in script else True),
              patch('seed_map_view.center', return_value=True) as center,
              patch('seed_map_view.show_route') as route,
              patch('seed_map_view.set_status') as status,
              patch('seed_map_view.asyncio.sleep', new_callable=AsyncMock)):
            game.return_value.foreground_game.return_value = 1
            game.return_value.focused.return_value = True
            asyncio.run(follow([(window, True, closed)], config, stop))
        self.assertEqual(center.call_count, 2)
        self.assertTrue(all(call.args[1] == (-566, 658) for call in center.call_args_list))
        self.assertEqual(route.call_count, 2)
        self.assertIn('Nether', status.call_args.args[1])

    def test_frameless_maps_leave_canvas_dragging_to_the_map(self):
        for mode in ('overlay', 'display'):
            with self.subTest(mode=mode):
                config = dict(mode=mode, seed='1', edition='bedrock', version='26.60–26.65',
                              dimension='Nether', x=None, z=None, follow=True,
                              target_display=dict(left=0, top=0, width=1920, height=1080))
                window = Mock(events=SimpleNamespace(before_show=Event(), closed=Event()))
                with (patch('seed_map_view.sys.stdin', StringIO(json.dumps(config))),
                      patch('seed_map_view.webview.create_window', return_value=window) as create,
                      patch('seed_map_view.webview.start')):
                    main()
                self.assertTrue(create.call_args.kwargs['frameless'])
                self.assertFalse(create.call_args.kwargs['easy_drag'])
                self.assertIn('dimension=nether', create.call_args.args[1])
