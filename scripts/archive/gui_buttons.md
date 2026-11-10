# Archive: GUI buttons taken out of the control window

Code that was removed from `main.py` and may be wanted again. Nothing here is imported or run, and `tools/check_constants.py` and pyflakes do not look at this folder: the snippets are from `AdoptMeGUI`
(`self` is the window) and use names of `main.py` (`cfg`, `tk`, `respawn_character`, `leave_and_rejoin`, `equip_task_pet`, `restore_pet_filters`, `run_async`). The functions they call are still in `main.py`
except `switch_pen_now()`, which is archived below too. Colours of the buttons: `GUI_RESPAWN_COLOR = "#8e44ad"` (purple), `GUI_TEST_COLOR = "#e74c3c"` (red); `GUI_RESPAWN_BUTTON_HEIGHT = 1` (lines).

## The Main tab: the "Functions" heading and its two purple buttons (Respawn, Leave & rejoin)

```python
        tk.Label(btn_frame, text="Functions", font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE, "bold"),
                 bg=self.bg, fg=self.accent).pack(anchor=tk.W)

        for label, action in (("Respawn", respawn_character), ("Leave & rejoin", leave_and_rejoin)):
            btn = tk.Button(btn_frame, text=label, command=lambda a=action: self.run_async(a),  # type: ignore[misc]
                            font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE), bg=cfg.GUI_RESPAWN_COLOR, fg=self.fg,
                            height=cfg.GUI_RESPAWN_BUTTON_HEIGHT, cursor="hand2")
            btn.pack(fill=tk.X, pady=cfg.GUI_WIDGET_SPACING)
            self.action_buttons.append(btn)
```

## The Debug tab's Options subtab: four red buttons (Equip a neon pet, Equip a common pet, Filters and pet back to normal, Switch the Pet Pen's full-grown pets now)

```python
        for label, kind in (("Equip a neon pet (for the daily tasks)", "neon"), ("Equip a common pet (for the daily tasks)", "common")):
            task_button = tk.Button(parent, text=label, command=lambda k=kind: self.run_async(lambda: equip_task_pet(k)), font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE),  # type: ignore[misc]
                                    bg=cfg.GUI_TEST_COLOR, fg=self.fg, cursor="hand2")
            task_button.pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING, pady=cfg.GUI_WIDGET_SPACING)
            self.action_buttons.append(task_button)
        restore_button = tk.Button(parent, text="Filters and pet back to normal (after a task)", command=lambda: self.run_async(restore_pet_filters), font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE),
                                   bg=cfg.GUI_TEST_COLOR, fg=self.fg, cursor="hand2")
        restore_button.pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING, pady=cfg.GUI_WIDGET_SPACING)
        self.action_buttons.append(restore_button)
        pen_button = tk.Button(parent, text="Switch the Pet Pen's full-grown pets now", command=lambda: self.run_async(switch_pen_now), font=(cfg.GUI_FONT, cfg.GUI_SECTION_FONT_SIZE),
                               bg=cfg.GUI_TEST_COLOR, fg=self.fg, cursor="hand2")
        pen_button.pack(fill=tk.X, padx=cfg.GUI_WIDGET_SPACING, pady=cfg.GUI_WIDGET_SPACING)
        self.action_buttons.append(pen_button)
```

## `switch_pen_now()`, the function of the last of them, and `pen_note()`, which kept what the pen's switch said for it to print in the console

```python
def switch_pen_now() -> bool:
    """The Debug tab's "Switch the Pet Pen's full-grown pets now": switch_pen_pets() at once, whether or not a growing up was seen or the pen is switched on, then a respawn back in the house (as Setup's check does); what it
    did is in the run log (`pet pen: ...`) and, for a pet that did not go out, a failure with its screenshot. True when the pen is as it should be."""
    state.pen_notes.clear()
    with task_unscrew(interruptible=False, what="debug: switch the pen"):
        done = switch_pen_pets()
        update_game_config(lambda c: c.update(pet_pen_swap_tried=time.time(), **({"pet_pen_swap": False} if done else {})))
        respawn_character()
    logger.info("Pet Pen: " + ("; ".join(state.pen_notes) if state.pen_notes else "nothing was said") + (" - done" if done else " - NOT as it should be"))
    return done

def pen_note(text: str) -> None:
    """A line of the pen's switch, in the run log and kept in state.pen_notes (switch_pen_now() shows them in the console)."""
    state.pen_notes.append(text)
    log_run_event(text)
```
