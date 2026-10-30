"""Prompt for naming a chat after its first questions."""

TITLE_SYSTEM = (
    "You create short titles for chat conversations. Reply with only a 2-5 word title in "
    "Title Case that names the topic. Never answer the question."
)
# Few-shot as chat turns, on topics unrelated to real chats so the small model doesn't copy them
TITLE_SHOTS = [
    ("Questions: How do I bake sourdough bread?", "Sourdough Bread Baking"),
    ("Questions: hello | what are the symptoms of flu?", "Flu Symptoms"),
]
