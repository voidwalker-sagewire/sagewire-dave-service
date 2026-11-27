"""Medication identity and routing regression checks; no clinical pass claimed."""
import unittest

from dave_medications import (
    BY_KEY, named_products, medication_context, medication_references,
    medication_instructions, product_information_reply,
)
from dave_conversation import parse_grounded_answer
import json


class MedicationIdentityTests(unittest.TestCase):
    def reply(self, question, history=(), **kwargs):
        keys = medication_context(question, list(history))
        ids = {key: f"V{i}" for i, key in enumerate(keys, 1)}
        raw = product_information_reply(question, keys, ids, history=history, **kwargs)
        if raw:
            references = {ids[key]: BY_KEY[key].source for key in keys}
            return parse_grounded_answer(json.dumps(raw), references)
        return None

    def test_spelled_kp_resolves_even_with_bad_initial_dictation(self):
        answer, sources, _ = self.reply("What is Jackson KP d-r-a-x-x-i-n-k-p used for?")
        self.assertIn("tulathromycin", answer)
        self.assertIn("ketoprofen", answer)
        self.assertIn("bovine respiratory disease", answer)
        self.assertEqual(sources, [BY_KEY["draxxin_kp"].source])

    def test_names_do_not_merge_kp_plain_or_alternate_formulations(self):
        for text, expected in (
            ("Draxxin KP", ["draxxin_kp"]),
            ("Draxxin", ["draxxin"]),
            ("Draxxin 25", []),
            ("Banamine Transdermal", []),
            ("Banamine paste", []),
            ("Vitameg", []),
            ("Jackson KP", []),
        ):
            with self.subTest(text=text):
                self.assertEqual(named_products(text), expected)

    def test_user_correction_survives_long_assistant_misinformation(self):
        history = [{"role": "user", "content": "The product is Draxxin KP."}]
        history.extend({"role": "assistant", "content": "Vetameg is a vitamin."} for _ in range(30))
        self.assertEqual(medication_context("What does the KP do?", history), ["draxxin_kp"])
        answer, *_ = self.reply("What does the KP do?", history)
        self.assertIn("ketoprofen", answer)
        self.assertNotIn("vitamin", answer)

    def test_vetameg_banamine_correction_names_duplicate_drug(self):
        answer, sources, _ = self.reply("Vetameg / Banamine")
        self.assertIn("flunixin meglumine", answer)
        self.assertIn("have you given both", answer)
        self.assertEqual(set(sources), {BY_KEY["vetameg"].source, BY_KEY["banamine"].source})
        self.assertNotIn("You can give both", answer)

    def test_product_education_does_not_require_weight_or_dose(self):
        answer, *_ = self.reply("What is Vetameg used for?")
        self.assertIn("IV-only", answer)
        self.assertIn("not a vitamin", answer)
        self.assertNotIn("weight?", answer)
        self.assertNotIn("mg/kg", answer)

    def test_acute_or_individual_treatment_questions_stay_on_clinical_path(self):
        history = [{"role": "user", "content": "Draxxin KP"}]
        for text in (
            "How much Draxxin KP for a 950 lb steer?",
            "What does KP do? My bull is down and cannot breathe.",
            "What is Draxxin KP used for? He cannot stand.",
            "Vetameg Banamine overdose",
            "What if I already gave Vetameg and Banamine?",
            "Can I give Draxxin KP with Banamine?",
            "What is the withdrawal for Draxxin KP?",
            "Draxxin KP for pinkeye by dart?",
            "What is Banamine Transdermal?",
            "What is ExampleDrug?",
            "Not Draxxin KP; another product.",
        ):
            with self.subTest(text=text):
                self.assertIsNone(self.reply(text, history))

    def test_unknown_product_breaks_implicit_lookup_focus(self):
        history = [{"role": "user", "content": "Draxxin KP"},
                   {"role": "user", "content": "Now I have another bottle, ExampleDrug."}]
        self.assertIsNone(self.reply("What does it do?", history))

    def test_current_image_never_bypassed_by_product_name_lookup(self):
        self.assertIsNone(self.reply("Draxxin KP", image_present=True))

    def test_kp_followup_cannot_latch_onto_other_product(self):
        history = [{"role": "user", "content": "Vetameg"}]
        self.assertIsNone(self.reply("What does the KP do?", history))

    def test_followup_answers_the_specific_question_without_repeating_full_card(self):
        history = [{"role": "user", "content": "Draxxin KP"}]
        general, *_ = self.reply("What is Draxxin KP used for?")
        followup, *_ = self.reply("What does the KP do?", history)
        self.assertTrue(followup.startswith("**KP is ketoprofen**"))
        self.assertLess(len(followup), len(general))
        pain, *_ = self.reply("Does it help with pain or inflammation?", history)
        self.assertTrue(pain.startswith("**Yes"))

    def test_treatment_context_includes_verified_labels_and_no_claim_of_administration(self):
        keys = medication_context("Can I use it?", [
            {"role": "user", "content": "Vetameg and Banamine"},
            {"role": "assistant", "content": "Draxxin KP was also given."},
        ])
        self.assertEqual(set(keys), {"vetameg", "banamine"})
        references = medication_references(keys)
        self.assertTrue(all(meta["label_review_date"] == "2026-11-27" for _, meta in references))
        self.assertIn("not proof they were given together", medication_instructions(keys))
        self.assertIn("IV-only cattle", next(doc for doc, meta in references if meta["medication_id"] == "banamine"))


if __name__ == "__main__":
    unittest.main()
