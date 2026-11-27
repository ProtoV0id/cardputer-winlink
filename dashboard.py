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


def draw_radio_home(draw, items, selected, body, small, fit, colors, right_top=0):
    """Draw stable mail and control columns from the original home actions.

    The control column scrolls through the session and system actions. Its
    squares show selection only; they do not imply a radio connection state.
    """
    panel, edge = colors["panel"], colors["edge"]
    draw.rounded_rectangle((6, 32, 171, 143), radius=5, fill=panel, outline=edge)
    draw.rounded_rectangle((177, 32, 313, 143), radius=5, fill=panel, outline=edge)
    left_indexes = range(min(4, len(items)))
    draw.text((14, 38), "MAIL", font=small, fill=colors["muted"])
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

    right_count = max(0, len(items) - 4)
    right_top = max(0, min(right_top, max(0, right_count - 4)))
    draw.text((185, 38), "CONTROL", font=small, fill=colors["muted"])
    if right_count:
        cue = f"{right_top + 1}-{min(right_top + 4, right_count)}/{right_count}"
        draw.text((304 - small.getlength(cue), 38), cue, font=small, fill=colors["muted"])
    draw.line((185, 50, 304, 50), fill=edge)
    if right_count > 4:
        draw.line((309, 53, 309, 138), fill=edge, width=2)
        thumb_h = max(8, int(85 * 4 / right_count))
        thumb_y = 53 + int((85 - thumb_h) * right_top / (right_count - 4))
        draw.line((309, thumb_y, 309, thumb_y + thumb_h), fill=colors["accent"], width=2)
    for row, index in enumerate(range(4 + right_top, min(8 + right_top, len(items)))):
        y = 53 + row * 22
        label = items[index][0]
        title = TILES.get(label, (label, ""))[0]
        active = index == selected
        draw.rounded_rectangle((184, y, 304, y + 19), radius=3,
                               fill=colors["selected"] if active else panel,
                               outline=colors["accent"] if active else edge)
        draw.rectangle((189, y + 4, 193, y + 15), fill=colors["accent"] if active else edge)
        draw.text((198, y), fit(title, 100, body), font=body,
                  fill=colors["text"] if active else colors["muted"])
