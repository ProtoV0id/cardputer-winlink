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
