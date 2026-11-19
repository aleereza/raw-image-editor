"""Culling: the app's suggestions, the three piles and the trust dial, the owner's decisions, and Done culling.

Plan §4.4: every photo gets a suggestion (keep or reject), a kind (technical, duplicates, eyes, expression,
composition) and a one-line reason. A photo goes straight to the auto-reject or auto-keep pile only when its kind
is set to automatic on the trust dial and the app is confident; everything else is the owner's call. Every action
is logged, and Done culling records the final call for each photo, the training data for the personal model.
"""

KINDS = ("technical", "duplicates", "eyes", "expression", "composition")
PILES = ("your_call", "auto_reject", "auto_keep")
