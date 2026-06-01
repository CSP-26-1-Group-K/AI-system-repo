from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import torch as th


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import omnigibson as og
from omnigibson.envs.hdf5_data_wrapper import HDF5PlaybackWrapper
from omnigibson.macros import gm
from omnigibson.utils.python_utils import h5py_group_to_torch


def replay_episode(wrapper: HDF5PlaybackWrapper, episode_id: int, *, realtime: bool, fps: float):
    data_grp = wrapper.input_hdf5["data"]
    traj_grp = data_grp[f"demo_{episode_id}"]
    traj = h5py_group_to_torch(traj_grp)
    action = traj["action"]
    state = traj["state"]
    state_size = traj["state_size"]

    wrapper.scene.restore(wrapper.scene_file, update_initial_file=True)
    og.sim.stop()
    for attr, vals in traj["init_metadata"].items():
        assert len(vals) == wrapper.scene.n_objects
    for i, obj in enumerate(wrapper.scene.objects):
        for attr, vals in traj["init_metadata"].items():
            val = vals[i]
            setattr(obj, attr, val.item() if val.ndim == 0 else val)
    og.sim.play()
    wrapper.reset()

    for robot in wrapper.robots:
        robot.control_enabled = False

    frame_delay = 1.0 / fps if fps > 0 else 0.0
    print(f"[hdf5-replay] Playing demo_{episode_id}: {len(action)} frames at {fps:g} fps")
    for i, (a, s, ss) in enumerate(zip(action, state, state_size)):
        if i % 30 == 0:
            print(f"[hdf5-replay] frame {i}/{len(action)}")
        frame_start = time.perf_counter()
        og.sim.load_state(s[: int(ss)], serialized=True)
        for obj in wrapper.scene.objects:
            obj.keep_still()
        wrapper.env.step(action=a, n_render_iterations=1)
        if realtime and frame_delay > 0:
            elapsed = time.perf_counter() - frame_start
            if elapsed < frame_delay:
                time.sleep(frame_delay - elapsed)
    print("[hdf5-replay] Playback finished. Window remains open; press Ctrl+C in the terminal to quit.")
    while True:
        og.sim.render()
        time.sleep(0.05)


def rollout_actions(wrapper: HDF5PlaybackWrapper, episode_id: int, *, realtime: bool, fps: float):
    data_grp = wrapper.input_hdf5["data"]
    traj_grp = data_grp[f"demo_{episode_id}"]
    traj = h5py_group_to_torch(traj_grp)
    action = traj["action"]

    wrapper.scene.restore(wrapper.scene_file, update_initial_file=True)
    wrapper.reset()

    frame_delay = 1.0 / fps if fps > 0 else 0.0
    print(f"[hdf5-replay] Rolling out demo_{episode_id}: {len(action)} actions at {fps:g} fps")
    for i, a in enumerate(action):
        if i % 30 == 0:
            print(f"[hdf5-replay] action {i}/{len(action)}")
        frame_start = time.perf_counter()
        wrapper.env.step(action=a, n_render_iterations=1)
        if realtime and frame_delay > 0:
            elapsed = time.perf_counter() - frame_start
            if elapsed < frame_delay:
                time.sleep(frame_delay - elapsed)
    print("[hdf5-replay] Action rollout finished. Window remains open; press Ctrl+C in the terminal to quit.")
    while True:
        og.sim.render()
        time.sleep(0.05)


def summarize(path: Path):
    import h5py

    with h5py.File(path, "r") as f:
        cfg = json.loads(f["data"].attrs["config"])
        scene = cfg.get("scene", {})
        robot = (cfg.get("robots") or [{}])[0]
        print("[hdf5-replay] input:", path)
        print("[hdf5-replay] scene_model:", scene.get("scene_model"))
        print("[hdf5-replay] scene_file:", scene.get("scene_file"))
        print("[hdf5-replay] robot:", robot.get("model"), "action_normalize=", robot.get("action_normalize"))
        print("[hdf5-replay] episodes:", f["data"].attrs.get("n_episodes"), "steps:", f["data"].attrs.get("n_steps"))
        for key in f["data"].keys():
            if key.startswith("demo_"):
                demo = f["data"][key]
                print("[hdf5-replay]", key, "samples=", demo.attrs.get("num_samples"), "action=", demo["action"].shape)


def main():
    parser = argparse.ArgumentParser(description="Play a collaborator HDF5 replay in OmniGibson.")
    parser.add_argument("input", type=Path, help="Path to the HDF5 replay file.")
    parser.add_argument("--episode", type=int, default=0)
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--no-realtime", action="store_true")
    parser.add_argument("--include-contacts", action="store_true", help="Preserve contact-enabled object state layout.")
    parser.add_argument("--include-task", action="store_true", help="Load the recorded task instead of DummyTask.")
    parser.add_argument("--include-robot-control", action="store_true", help="Keep recorded robot controllers enabled.")
    parser.add_argument("--official-playback", action="store_true", help="Use OmniGibson's built-in playback loop.")
    parser.add_argument("--action-rollout", action="store_true", help="Replay actions without restoring every recorded state.")
    parser.add_argument("--output", type=Path, default=Path("logs/replay_playback_output.hdf5"))
    args = parser.parse_args()

    input_path = args.input.resolve()
    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    summarize(input_path)

    gm.ENABLE_TRANSITION_RULES = False
    gm.USE_GPU_DYNAMICS = False
    wrapper = HDF5PlaybackWrapper.create_from_hdf5(
        input_path=str(input_path),
        output_path=str(output_path),
        include_task=args.include_task,
        include_task_obs=args.include_task,
        include_robot_control=args.include_robot_control,
        include_contacts=args.include_contacts,
        n_render_iterations=1,
        flush_every_n_traj=1,
        robot_obs_modalities=(),
    )
    if args.official_playback:
        print(f"[hdf5-replay] Playing demo_{args.episode} with OmniGibson built-in playback")
        wrapper.playback_episode(args.episode, record_data=False)
        print("[hdf5-replay] Playback finished. Window remains open; press Ctrl+C in the terminal to quit.")
        while True:
            og.sim.render()
            time.sleep(0.05)
    elif args.action_rollout:
        rollout_actions(wrapper, args.episode, realtime=not args.no_realtime, fps=args.fps)
    else:
        replay_episode(wrapper, args.episode, realtime=not args.no_realtime, fps=args.fps)


if __name__ == "__main__":
    main()
