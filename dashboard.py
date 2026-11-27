"""Compact, keyboard-driven home screen for PocketLink's 320x170 display."""

# Short titles are keyed by the existing action labels so callbacks and their
# order remain identical to the classic menu. Subtitles are descriptive only.
TILES = {
    "Inbox": ("Inbox", "Read messages"),
    "Compose message": ("Compose", "Write a message"),
    "Outbox": ("Outbox", "Queued messages"),
    "Sent messages": ("Sent", "Sent messages"),
    "Connect via Telnet": ("Telnet", "Internet session"),
    "Connect via packet": ("Packet", "Radio session"),
    "Packet radio settings": ("Radio setup", "Gateway and radio"),
    "Settings": ("Settings", "Account and layout"),
    "About / help": ("Help", "Controls and info"),
    "Exit": ("Exit", "Close PocketLink"),
}


def draw_dashboard(draw, items, selected, body, small, fit, colors):
    """Draw a page of four actions, keeping the original menu selection."""
    page_start = (selected // 4) * 4
    for cell, (label, _) in enumerate(items[page_start:page_start + 4]):
        x = 7 + (cell % 2) * 155
        y = 33 + (cell // 2) * 55
        active = page_start + cell == selected
        draw.rounded_rectangle((x, y, x + 147, y + 49), radius=5,
                               fill=colors["selected"] if active else colors["panel"],
                               outline=colors["accent"] if active else colors["edge"])
        if active:
            draw.rectangle((x + 2, y + 6, x + 4, y + 43), fill=colors["accent"])
        title, subtitle = TILES.get(label, (label, ""))
        draw.text((x + 11, y + 5), fit(title, 125, body), font=body, fill=colors["text"])
        draw.text((x + 11, y + 29), fit(subtitle, 125, small), font=small, fill=colors["muted"])


def draw_radio_home(draw, items, selected, body, small, fit, colors):
    """Split the original actions into mail, session, and system panels.

    All callbacks and their ordering belong to the original home menu. The
    left panel changes to system actions after the session actions, while the
    right panel continues to show the session controls. Neutral squares on
    the session buttons are decorative and never report connection state.
    """
    panel, edge = colors["panel"], colors["edge"]
    draw.rounded_rectangle((6, 32, 171, 143), radius=5, fill=panel, outline=edge)
    draw.rounded_rectangle((177, 32, 313, 143), radius=5, fill=panel, outline=edge)
    system_page = selected >= 7
    left_indexes = range(7, min(10, len(items))) if system_page else range(min(4, len(items)))
    draw.text((14, 38), "SYSTEM" if system_page else "MAIL", font=small, fill=colors["muted"])
    cue = "MAIL / SESSION" if system_page else "MORE: F/X"
    draw.text((163 - small.getlength(cue), 38), cue, font=small, fill=colors["muted"])
    draw.line((14, 50, 163, 50), fill=edge)
    for row, index in enumerate(left_indexes):
        y = 53 + row * 20
        label = items[index][0]
        title = TILES.get(label, (label, ""))[0]
        active = index == selected
        if active:
            draw.rounded_rectangle((10, y - 1, 166, y + 17), radius=3,
                                   fill=colors["selected"], outline=edge)
            draw.rectangle((11, y + 2, 13, y + 14), fill=colors["accent"])
        elif row < len(left_indexes) - 1:
            draw.line((19, y + 17, 159, y + 17), fill=edge)
        draw.text((21, y - 1), fit(title, 140, body), font=body,
                  fill=colors["text"] if active else colors["muted"])

    draw.text((185, 38), "SESSION CONTROL", font=small, fill=colors["muted"])
    draw.line((185, 50, 304, 50), fill=edge)
    for row, index in enumerate(range(4, min(7, len(items)))):
        y = 55 + row * 27
        label = items[index][0]
        title = TILES.get(label, (label, ""))[0]
        active = index == selected
        draw.rounded_rectangle((184, y, 306, y + 23), radius=3,
                               fill=colors["selected"] if active else panel,
                               outline=colors["accent"] if active else edge)
        draw.rectangle((190, y + 5, 195, y + 18), fill=colors["accent"] if active else edge)
        descriptor = {"Connect via Telnet": "Internet", "Connect via packet": "Radio"}.get(label, "")
        draw.text((200, y + 1), fit(title, 58 if descriptor else 100, body), font=body,
                  fill=colors["text"] if active else colors["muted"])
        if descriptor:
            draw.text((302 - small.getlength(descriptor), y + 6), descriptor,
                      font=small, fill=colors["muted"])
