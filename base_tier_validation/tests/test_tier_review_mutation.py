# Copyright 2026 Ledo Enterprises
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from odoo import Command
from odoo.exceptions import AccessError, ValidationError
from odoo.tests import tagged

from .common import CommonTierValidation


@tagged("post_install", "-at_install")
class TierReviewMutation(CommonTierValidation):
    def _request(self, value=1.0):
        record = self.test_model.with_user(self.test_user_2).create(
            {"test_field": value}
        )
        record.invalidate_recordset()
        record.request_validation()
        self.assertTrue(record.review_ids)
        return record

    def _wizard(self, record, reviews, user=None):
        return (
            self.env["comment.wizard"]
            .with_user(user or self.test_user_1)
            .create(
                {
                    "res_model": record._name,
                    "res_id": record.id,
                    "review_ids": [Command.set(reviews.ids)],
                    "validate_reject": "validate",
                    "comment": "Reviewed evidence",
                }
            )
        )

    def test_preparer_cannot_forge_decision_identity_or_context(self):
        record = self._request()
        review = record.review_ids.with_user(self.test_user_2)
        for values in (
            {"status": "approved", "done_by": self.test_user_1.id},
            {"requested_by": self.test_user_1.id},
            {"comment": "forged"},
        ):
            with self.assertRaises(AccessError), self.env.cr.savepoint():
                review.with_context(tier_review_token=True).write(values)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            review.unlink()
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            record.action_confirm()

    def test_direct_creation_or_copy_cannot_inject_an_approved_tier(self):
        record = self._request()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            self.env["tier.review"].with_user(self.test_user_2).create(
                {
                    "model": record._name,
                    "res_id": record.id,
                    "definition_id": self.tier_definition.id,
                    "status": "approved",
                    "done_by": self.test_user_1.id,
                }
            )
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            record.review_ids.with_user(self.test_user_1).copy()

    def test_reviewer_uses_document_action_and_actor_is_recorded(self):
        record = self._request()
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            record.review_ids.with_user(self.test_user_1).write({"status": "approved"})
        record.with_user(self.test_user_1).validate_tier()
        self.assertEqual(record.review_ids.done_by, self.test_user_1)
        record.action_confirm()
        self.assertEqual(record.state, "confirmed")

    def test_comment_wizard_cannot_decide_another_document(self):
        first, second = self._request(), self._request()
        wizard = self._wizard(first, second.review_ids)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            wizard.add_comment()
        self.assertEqual(second.review_ids.status, "pending")
        self.assertFalse(second.review_ids.comment)

    def test_comment_wizard_checks_current_reviewer(self):
        record = self._request()
        wizard = self._wizard(record, record.review_ids, self.test_user_2)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            wizard.add_comment()
        self.assertFalse(record.review_ids.done_by)

    def test_comment_wizard_cannot_skip_an_earlier_tier(self):
        record = self._request(4.0)
        later = record.review_ids.filtered(lambda r: self.test_user_2 in r.reviewer_ids)
        self.assertTrue(later)
        self.assertTrue(all(r.status == "waiting" for r in later))
        wizard = self._wizard(record, later, self.test_user_2)
        with self.assertRaises(AccessError), self.env.cr.savepoint():
            wizard.add_comment()

    def test_valid_comment_and_restart_preserve_normal_workflow(self):
        record = self._request()
        self._wizard(record, record.review_ids).add_comment()
        self.assertEqual(record.review_ids.status, "approved")
        self.assertEqual(record.review_ids.comment, "Reviewed evidence")
        record.restart_validation()
        self.assertFalse(record.review_ids)
        record.invalidate_recordset()
        record.request_validation()
        self.assertEqual(record.review_ids.status, "pending")

    def test_document_deletion_can_remove_its_own_reviews(self):
        record = self._request()
        reviews = record.review_ids
        record.unlink()
        self.assertFalse(reviews.exists())
