# A dimmer that is on at exact level 0 (2026-11-20)

Live captures of one F418 dimmer (`WHERE 0010`) on a bus with an F461 2.0.11 and an F455 1.0.76, one Home Assistant
instance per gateway. `frames.json` holds one list per trace: `[time, direction, frame]`, local time (UTC+2). Only the
`WHO = 1` frames of that dimmer, taken from an instance's debug log: `tx` a frame the instance sent (logged when the
gateway acknowledged it), `rx` a frame it received. No address, MAC or name of the plant.

What they show:

- `f455_on_at_level_0`, `f461_on_at_level_0`: the dimmer was off and remembered level 0 (how it got there is not known:
  dim-down steps stop at 1 %, a level 0 write gives 1 %, off with a speed keeps the level). Switched on, it reports
  `*1*2*0010##` and answers the level read with `*#1*0010*1*100*10##`: on, at level 0, in `DIMENSION 1`. Twelve seconds
  later its status is still `*1*2*0010##`.
- `f461_levels`: the same state forty minutes later, read three times in 12 s. Off is `*1*0*0010##` with a `DIMENSION 4`
  level 0 (`*#1*0010*4*100*5##`). Then the level written to 1, 2, 5, 10 and 20 %.
- `f461_dim_steps`: dim-down steps from 20 % end at 1 % (`*#1*0010*1*101*10##`).
- `f461_level_0_write_through_f455`: a level 0 write acknowledged by the F455 leaves the dimmer on at 1 %.
- `f461_off_with_speed`: off with a speed is announced by a `DIMENSION 4` level 0 (`*#1*0010*4*100*2##`).

What each trace is, and how it was taken, is in `frames.json` under `_traces`.

Replayed by `tests/test_dimmer_on_at_level_0_2026_11_20.py`.

`review_frames.json` holds the frames the maintainer quoted in the review of PR 970 (an MH200 2.1.0 and an MH200N 1.1.8,
other plants): on those gateways a dimmer that goes off with a ramp reports only `DIMENSION 1` level 0. Copied from
the comments; the captures themselves are not in this repository.
