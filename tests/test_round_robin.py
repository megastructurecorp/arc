import unittest

from megahub.examples.round_robin_handler import build_round_reply


class TestRoundRobinHandler(unittest.TestCase):
    def test_build_round_reply_only_runs_on_owned_turn(self):
        message = {
            "id": 10,
            "body": "Kick off a 10-round coordination loop.",
            "metadata": {
                "loop": {
                    "participants": ["claude-code", "codex-agent"],
                    "next_agent": "claude-code",
                    "remaining_turns": 10,
                    "turn_number": 0,
                }
            },
        }

        self.assertIsNone(
            build_round_reply(
                message,
                agent_id="codex-agent",
                agent_name="Codex Agent",
                mode="concise",
            )
        )

        reply = build_round_reply(
            message,
            agent_id="claude-code",
            agent_name="Claude Code",
            mode="concise",
        )
        self.assertIsNotNone(reply)
        self.assertEqual(reply["metadata"]["loop"]["next_agent"], "codex-agent")
        self.assertEqual(reply["metadata"]["loop"]["remaining_turns"], 9)
        self.assertEqual(reply["metadata"]["loop"]["turn_number"], 1)

    def test_build_round_reply_stops_when_turns_are_exhausted(self):
        message = {
            "id": 21,
            "body": "Final turn.",
            "metadata": {
                "loop": {
                    "participants": ["claude-code", "codex-agent"],
                    "next_agent": "codex-agent",
                    "remaining_turns": 1,
                    "turn_number": 9,
                }
            },
        }

        reply = build_round_reply(
            message,
            agent_id="codex-agent",
            agent_name="Codex Agent",
            mode="review",
        )
        self.assertIsNotNone(reply)
        self.assertEqual(reply["metadata"]["loop"]["next_agent"], "claude-code")
        self.assertEqual(reply["metadata"]["loop"]["remaining_turns"], 0)
        self.assertIn("Loop complete", reply["body"])


if __name__ == "__main__":
    unittest.main()
