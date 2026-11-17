"""Starting templates offered on the Write page ("Start from: ...").

Each template's HTML uses only tags that rich_text.clean_html allows, so
an entry started from one saves exactly like any other formatted entry.
"""

WRITING_TEMPLATES = [
    {
        "key": "gratitude",
        "name": "Gratitude",
        "html": "<h3>Three things I'm grateful for</h3><ol><li></li><li></li><li></li></ol>"
                "<h3>Why they mattered</h3><p></p>",
    },
    {
        "key": "morning-pages",
        "name": "Morning pages",
        "html": "<h3>How I feel this morning</h3><p></p><h3>What matters today</h3><ul><li></li></ul>"
                "<h3>One thing I'm worried about</h3><p></p>",
    },
    {
        "key": "brain-dump",
        "name": "Brain dump",
        "html": "<h3>Everything on my mind</h3><ul><li></li></ul><h3>What I can let go of</h3><p></p>",
    },
    {
        "key": "evening-check-in",
        "name": "Evening check-in",
        "html": "<h3>Today in a sentence</h3><p></p><h3>Best moment</h3><p></p>"
                "<h3>What I learned</h3><p></p>",
    },
]
