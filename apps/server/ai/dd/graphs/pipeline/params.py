"""Tunables shared by the planner and the synth graphs."""
from __future__ import annotations


# Tutorial chapters (pipeline v2): the planner sizes a chapter by the best code it teaches and writes its reading time into the plan; the synth outline plans one
# step per best code, a capstone application ends the chapter, and the judge adds the gates of a complete tutorial (every best code taught, a capstone that
# parses and uses the steps, a reading time in budget). Off keeps the v1 pipeline exactly. Turn it on once the capstone designer has been seen working on a real
# model pool, then re-plan and re-write the chapters.
TUTORIAL_MODE = False
