from __future__ import annotations

import reminder_language_extensions as layer


def test_natural_user_name_questions_route_to_memory() -> None:
    for text in (
        "شو اسمي أنا؟",
        "بتعرف شو اسمي؟",
        "بتتذكر شو اسمي؟",
        "Wie heiße ich denn?",
        "Weißt du wie ich heiße?",
        "What is my name again?",
        "Do you remember what my name is?",
        "Як мене звати знову?",
        "Θυμάσαι πως με λένε;",
    ):
        assert layer._is_user_name_question(text), text


def test_sam_name_questions_do_not_route_to_user_memory() -> None:
    for text in (
        "شو اسمك انت؟",
        "Wie heißt du denn?",
        "What is your name?",
    ):
        assert not layer._is_user_name_question(text), text
