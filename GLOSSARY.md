# Automagic

Automagic settles planning topics through an interview and groups them into
reviewable work items. See [planning terminology](README.md#how-planning-works).

## Language

**Planning interview**:
A guided clarification of a goal, its assumptions, and the decisions needed
to define the intended work.
See [planning questions](src/automagic/interview.py).

**Interview pause**:
A stop before planning is settled, with unresolved decisions retained for
deliberate continuation.
See [pause and continuation](src/automagic/cli.py).

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

**Work-item run preparation**:
The determination of a run's work-item scope and prerequisite ordering.
See [work-item selection](src/automagic/runner.py) and
[run preparation](src/automagic/cli.py).

**Run preview**:
A presentation of a run's work-item scope and prerequisite ordering, not a
guaranteed execution schedule.
See [run presentation](src/automagic/cli.py) and
[work-item execution](src/automagic/runner.py).
