"""Reviewed public task requirements; never load simulator scoring or online truth."""

from dataclasses import dataclass

VERSION = "robodojo-public-2026-09-12-v2"
BASE_URL = "https://robodojo-benchmark.com/doc/sim-tasks/"


@dataclass(frozen=True)
class PublicInstruction:
    slug: str
    instruction: str
    requirements: tuple[str, ...]
    source_sha256: str

    def as_message(self):
        return {
            "version": VERSION,
            "source_url": BASE_URL + self.slug + "/",
            "requirements": list(self.requirements),
            "scope": "Static public task requirements, not current progress or a supplied action plan.",
        }


REVIEWED = (
    PublicInstruction(
        "solve-equation",
        "Complete the equation by selecting the correct missing number or operator and placing it on the pad, then reset the robot arm.",
        (
            "Select the correct missing number or operator from the available pieces and place it on the missing pad to complete the equation.",
            "The public task requires each digit to remain within its permitted rotational tolerance relative to its initial pose.",
            "After completing the equation, return the robot to its episode-initial pose.",
        ),
        "d2612e20d259641719c335ba939f78b6904667ed4ea71f663bfe77c319a0705f",
    ),
    PublicInstruction(
        "align-blocks",
        "Use the set square to push the three blocks into a straight, aligned row, then reset the robot arm.",
        (
            "Use the set square to push the three blocks into a straight, parallel aligned row.",
            "The public task includes an episode-wide no-lift condition for the blocks; keep them on the table.",
            "After alignment, return the robot to its episode-initial pose.",
        ),
        "346eece139658231f5a1d153bf241849cbaa667f3fb1528034345df3da666656",
    ),
    PublicInstruction(
        "stack-blocks",
        "Stack the three blocks with different textures.",
        (
            "Stack all three blocks into a stable pile; a held block is not a released stack.",
            "After stacking, return the robot to its episode-initial pose.",
        ),
        "af1d75305fe011e2eeff40a9c37e6d22efcdd8f284d6a1ba2490c579c2f708bc",
    ),
    PublicInstruction(
        "deposit-coin",
        "Pick up the coin from the holder and insert it precisely into the coin bank.",
        (
            "Pick up the coin from its holder and insert the coin fully into the coin bank slot.",
            "After insertion, return the robot to its episode-initial pose.",
        ),
        "23928e91d35f23c1917243ddf2bbe492c4f76d2e056056ca6dd94b21707573e7",
    ),
    PublicInstruction(
        "fill-egg-holder",
        "Place the four eggs from the basket into the egg holder, then close the lid.",
        (
            "Transfer the four eggs one by one from the basket into the egg holder.",
            "Place and release the eggs correctly inside the holder near its bottom.",
            "After all four eggs are placed, close the holder lid correctly and fully.",
            "Then return the robot to its episode-initial pose.",
        ),
        "2368dbb508dcbb0224ff515dcc815a4c265d043d3afe74dd3172725669b6e609",
    ),
    PublicInstruction(
        "cover-blocks",
        "Cover the blocks from left to right, remember their colors, then uncover them in the order: red, green, and blue.",
        (
            "Cover all three blocks from left to right, remembering the color under each cover.",
            ("Only after all are covered, uncover red while green and blue remain covered, then green "
             "while blue remains covered, and finally blue."),
            "Maintain valid cup orientation throughout the covering and uncovering sequence.",
            "After the sequence, return the robot to its episode-initial pose.",
        ),
        "ca869895306aef68ce3c5c11c725b85af29b667697ba167f1f31c9cb354b9b9b",
    ),
)


def public_instruction(instruction):
    """Match only the reviewed runtime text; no task-name or fuzzy inference."""
    for entry in REVIEWED:
        if instruction == entry.instruction:
            return entry
    return None
