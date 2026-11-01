# Automagic

Automagic settles planning topics through an interview and groups them into
reviewable work items. See [planning terminology](README.md#how-planning-works).

## Language

**Interview durability**:
Preservation of agreed decisions, interview position, pending input, and
interview history across an interruption.
See [interview durability](src/automagic/durability.py) and
[recovery guarantees](docs/adr/0005-atomic-interview-checkpoints.md).

**Plan publication**:
The derived representation of agreed planning topics and their work-item
breakdown for readers.
See [plan publication](src/automagic/publication.py) and
[plan presentation](src/automagic/cli.py).
