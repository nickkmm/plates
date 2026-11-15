VERDICT: PASS

## Summary
- Round 2 applies @nickkmm's feedback: the used-plates photo is now required on every card, DETERIORO and PÉRDIDA, not only DETERIORO.
- The change is consistent across layers. `_validate_card` always requires `used_plates_file`, `_save_files` always stores the upload, and the form input is plain `required` again, so the show/hide JS and `DETERIORATION_REASON` are gone. Nothing is left over (grep finds no `used-plates-field` or `DETERIORATION_REASON` in app, tests or docs).
- Migration 0014, `init.sql`, the docstrings and comments, and the DATABASE, WEB and CORE docs are reworded to match, and the old "only DETERIORO / NULL on PÉRDIDA" claims are removed.
- Tests were rewritten to the new rule, not weakened. The unit, form and end-to-end tests are parametrized over both reasons, and a missing photo on a loss card is now rejected with 400.
- The staff download, ZIP and gallery code is unchanged and still correct, because the column stays nullable for older declarations.

## Findings
None.

## Notes
- This knowingly overrides spec requirements 5, 7, 8 and 10 and acceptance criteria 3 to 5, per the owner's feedback. The spec file itself was not updated, which is fine for this job.
- `tests/web/test_declarations.py:1169` has the comment "DETERIORO only". It describes an unrelated fixture, so it needs no change.
