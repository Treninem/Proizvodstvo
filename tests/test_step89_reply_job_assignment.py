from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.handlers.reply_job_assignment import _normalized_command, try_handle_reply_job_assignment


class ReplyJobAssignmentTests(unittest.IsolatedAsyncioTestCase):
    def test_short_commands_are_normalized(self) -> None:
        self.assertEqual(_normalized_command("  Должность! "), "должность")
        self.assertEqual(_normalized_command("Назначить   должность"), "назначить должность")

    async def test_reply_opens_created_job_titles(self) -> None:
        target = SimpleNamespace(
            id=2002,
            first_name="Иван",
            last_name="Иванов",
            username="worker",
            is_bot=False,
        )
        actor = SimpleNamespace(id=1001, first_name="Руководитель")
        chat = SimpleNamespace(id=-777, type="supergroup", title="Рабочая группа")
        reply = SimpleNamespace(from_user=target)
        message = SimpleNamespace(
            text="должность",
            from_user=actor,
            chat=chat,
            reply_to_message=reply,
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )
        jobs = [
            {"id": 11, "name": "Оператор"},
            {"id": 12, "name": "Мастер смены"},
        ]

        with (
            patch("app.handlers.reply_job_assignment.can_manage_accounting", new=AsyncMock(return_value=True)),
            patch("app.handlers.reply_job_assignment.repo.list_job_titles", return_value=jobs),
            patch("app.handlers.reply_job_assignment.repo.set_setup_session") as set_session,
        ):
            handled = await try_handle_reply_job_assignment(message)

        self.assertTrue(handled)
        set_session.assert_called_once()
        args = set_session.call_args.args
        self.assertEqual(args[0], actor.id)
        self.assertEqual(args[1], actor.id)
        self.assertEqual(args[2], "assign_job_select")
        self.assertEqual(args[3]["target_user_id"], target.id)
        self.assertEqual(args[3]["group_chat_id"], chat.id)

        message.answer.assert_awaited_once()
        call = message.answer.await_args
        self.assertIn("Вводить название вручную не нужно", call.args[0])
        keyboard = call.kwargs["reply_markup"]
        button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertIn("Оператор", button_texts)
        self.assertIn("Мастер смены", button_texts)
        self.assertIn("jobassign:pick:2002:11:0", callbacks)
        self.assertIn("jobassign:pick:2002:12:0", callbacks)

    async def test_without_reply_explains_what_to_do(self) -> None:
        actor = SimpleNamespace(id=1001)
        chat = SimpleNamespace(id=-777, type="group", title="Рабочая группа")
        message = SimpleNamespace(
            text="назначить должность",
            from_user=actor,
            chat=chat,
            reply_to_message=None,
            bot=SimpleNamespace(),
            answer=AsyncMock(),
        )
        handled = await try_handle_reply_job_assignment(message)
        self.assertTrue(handled)
        message.answer.assert_awaited_once()
        self.assertIn("Ответьте на сообщение сотрудника", message.answer.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
