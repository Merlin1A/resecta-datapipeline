"""Deterministic negative-text corpus for false-positive-rate measurement.

This corpus is the document-level false-positive-rate baseline fixture.
Every document is realistic
benign prose that contains **no valid PII**: any detection the engine produces
when scanning this corpus is, by construction, a false positive.

The content is engineered for the context-mandatory detector families
(account, phone, MRN, EIN, ITIN, routing). Each paragraph deliberately plants
the family trigger-keywords in innocent, non-PII contexts -- "gave an account
of the hearing", "phone home", "the routing of the parcel", "for the record",
"the medical team" -- so the measurement captures whether those detectors
over-fire on keyword-adjacent non-PII. That is the surface the planned learned
context scorer must suppress.

The corpus carries two flavors of benign document (see ``_PARAGRAPHS``):

1. **Keyword-only** paragraphs (the first 20 per doctype) seed the family
   trigger-keywords with no numerals at all.
2. **Number-shaped-but-invalid** paragraphs (the next 10 per doctype) place the
   same trigger-keywords *next to* short numeric content -- invoice/room/page/
   case numbers, years, small counts, percentages, comma-grouped dollar amounts
   -- so the measurement also exercises the *structural* detectors (account /
   phone / routing / EIN), which key off digit runs. This is the surface that
   over-fires on number-shaped NON-PII; a corpus with zero digits cannot stress
   it. The hard safety rule that keeps these a true negative is mechanical: no
   digit run reaches length 5, so no SSN (``\\d{3}\\d{2}\\d{4}``), phone
   (``\\d{10}``), account (``\\d{10}``), routing (``\\d{9}`` + mod-10), EIN
   (``\\d{2}\\d{7}``), ITIN, or credit-card shape can parse. Only runs of length
   1-4 appear (comma-grouped thousands like "1,250" keep each group <= 3).

License-clean conventions mirror ``resecta-sample-doc`` (see verify.py): no
SSN-shaped strings, no standalone 9-digit numbers, no
valid-checksum routing/EIN/account numbers, no credit-card shapes, phones only
in the reserved 555-01xx fictional range (none are planted here), domains only
RFC-2606, no real org/bank/agency/brand names, person names kept sparse and
clearly fictional, every character is printable ASCII. The corpus is a frozen literal -- no
RNG, no clock, minted constants -- so it is byte-deterministic by construction.

The artifact is a dev/eval fixture; it is NOT installed to the Swift Resources
path (it has no ``INSTALL_ROUTES`` entry, like ``g8_bucket_recall``). The Swift
baseline harness reads it from ``Fixtures/corpus/negative_corpus.json`` via
``Bundle.module``.

This module follows the pipeline's determinism (``common/determinism.py``)
and mechanism-language (``common/mechanism_language.py``) rules.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from resecta_data.common.determinism import CANONICAL_SEED
from resecta_data.common.mechanism_language import assert_safe

logger = logging.getLogger(__name__)

_MODULE_NAME: Final[str] = "resecta_data.corpus.negative.generate"
_SCHEMA_VERSION: Final[int] = 1
_ID_TEMPLATE: Final[str] = "neg_%06d"

# The five doctypes the engine classifies into; mirrors the G8 corpus's
# doctype enum (court / medical / financial / foia / generic).
_DOCTYPE_ORDER: Final[tuple[str, ...]] = (
    "court",
    "medical",
    "financial",
    "foia",
    "generic",
)

# Minted benign paragraphs, grouped by doctype (30 per doctype: 20 keyword-only
# followed by 10 number-shaped). Each is original prose -- not copied from any
# real document -- of roughly 40-120 words, and seeds the family trigger-
# keywords in non-PII contexts as the module docstring describes: "account" as
# a narrative report, "phone" as a verb / idiom ("phone home", "phone the
# office"), "routing" as logistics ("the routing of the parcel"), "record" as a
# file / transcript ("for the record"), and "medical" / "medicine" as a field
# of practice. The first 20 per doctype contain no numerals at all. The last 10
# per doctype plant the same keywords adjacent to SHORT numeric content (invoice
# / room / page / case numbers, years, small counts, percentages, comma-grouped
# dollar amounts) so the structural detectors are exercised too -- but every
# digit run is length 1-4, so the corpus still carries no SSN / account /
# routing / EIN / ITIN / MRN / phone shape (any of those needs a run of >= 5
# contiguous digits; the determinism + PII-absence guards in
# tests/corpus/test_negative.py pin "no digit run of length >= 5").
_PARAGRAPHS: Final[dict[str, tuple[str, ...]]] = {
    "court": (
        "The witness gave a careful account of the afternoon hearing, recalling "
        "that the parties had agreed to continue the matter before the recess. "
        "Counsel asked the clerk to read the relevant portion of the record back "
        "into the transcript so that the bench could follow the sequence of "
        "events. No party objected, and the judge noted for the record that the "
        "stipulation would stand. When the recess ended, the parties were told to "
        "phone the scheduling office if a continuance proved necessary, rather "
        "than wait for the next available date on the docket.",
        "In her closing, the advocate offered a measured account of how the "
        "dispute had unfolded, walking the panel through each filing in turn. The "
        "presiding officer reminded everyone that the routing of the case through "
        "the appellate division would depend on whether a timely notice was "
        "lodged. The deputy clerk, for the record, confirmed that the brief had "
        "been received and entered into the docket on the morning it was due. "
        "Both sides agreed that the summary fairly captured the procedural "
        "history without further amendment.",
        "The parties met in chambers to settle accounts of cost and time before "
        "the trial resumed. One attorney asked whether the court would phone the "
        "remaining witness, who lived a long way from the courthouse, or whether "
        "testimony by written declaration would suffice. The magistrate decided "
        "the latter was acceptable and asked that the declaration be added to the "
        "record. By midday the bench had a full account of the outstanding "
        "matters and set a brief schedule for the rest of the week.",
        "Opening argument framed the case as a simple question of who owed an "
        "account to whom, though the facts proved less tidy than that. The clerk "
        "explained the routing of exhibits between the two courtrooms so that "
        "nothing would be misplaced during the lunch recess. For the record, the "
        "bailiff confirmed that the jury had not yet been seated. The defense "
        "reserved the right to revisit the timeline, and the court accepted a "
        "short written account of the relevant dates from each side.",
        "After the hearing the parties were asked to phone ahead before "
        "delivering any supplemental materials, so the front desk could route "
        "them to the correct chamber. The law clerk kept a running account of "
        "every document that arrived, noting the date and the delivering party in "
        "the file. The judge thanked counsel for keeping the record clean and "
        "well ordered, which she said made the eventual ruling far easier to "
        "write. The session closed without further argument from either side.",
        "The expert testified at length, giving a plain account of the standard "
        "of care expected in an ordinary medical practice. Opposing counsel asked "
        "the witness to keep to the record and avoid speculation about matters "
        "not in evidence. The court managed the routing of the lengthy exhibit "
        "binder so that each juror could follow along. At the close of the day "
        "the reporter confirmed that the full account of testimony had been "
        "transcribed and would be available the following morning.",
        "A short procedural account opened the afternoon, summarizing what had "
        "been decided before the break. The clerk reminded the gallery that no "
        "phone use was permitted in the courtroom and asked that any calls be "
        "made in the corridor. The judge entered a brief note in the record about "
        "the revised schedule and asked both sides to confirm their availability. "
        "Neither party raised an objection, and the court adjourned with a clear "
        "account of the remaining steps.",
        "Wishing to set the record straight, the witness offered a fuller account "
        "of the meeting that the parties had described so differently. The deputy "
        "explained the routing of the sealed materials, which would be handled "
        "separately from the public file. Counsel agreed that the corrected "
        "account did not change the legal question before the court. The bench "
        "asked that the clarification be entered into the record and that the "
        "parties proceed to the next item without further delay.",
        "The mediator invited each side to give a brief account of what it hoped "
        "to achieve before the talks began in earnest. A note-taker kept the "
        "record so that any agreement could be written down at once. The parties "
        "were asked not to phone colleagues during the session, so the room could "
        "stay focused. By the afternoon they had a shared account of the points "
        "still open, and the mediator set a short plan to close the remaining "
        "gaps over the following week.",
        "The court administrator walked new staff through how the building kept "
        "its record of filings and how cases moved between rooms. She explained "
        "the routing of urgent motions so that nothing waited longer than it "
        "should. Each trainee gave a short account of the part of the process they "
        "found hardest to follow. By the end of the morning the group could "
        "explain the routing of a typical case and where each note belonged in "
        "the record.",
        "Before the verdict the foreperson asked for a plain account of how the "
        "deliberation should be recorded, so nothing would be missed. The clerk "
        "described the routing of the signed form to the bench and back. Jurors "
        "were reminded not to phone anyone about the case until they were "
        "discharged. The judge thanked the panel for its careful work and noted "
        "for the record that the proceedings had been orderly and fair from the "
        "first day to the last.",
        "A continuing-education session for clerks offered a clear account of "
        "recent changes to filing practice. The instructor explained the routing "
        "of electronic submissions and where the paper copy still belonged in the "
        "record. Attendees kept a short account of the points they wished to raise "
        "with their own offices later. The session closed with a reminder that a "
        "tidy record at intake saved a great deal of time when a case finally "
        "came on for hearing.",
        "Counsel rose to correct the record, offering a careful account of a date "
        "that had been stated in error during the morning. The clerk confirmed the "
        "correction and entered it so the file would be accurate. The judge asked "
        "the parties to phone the office if any further errors came to light "
        "before the next session. Both sides agreed that the corrected account "
        "left the legal question untouched, and the court moved on to the next "
        "item on the list.",
        "The settlement conference opened with a brief account of the offers "
        "already exchanged between the parties. The deputy explained the routing "
        "of any signed terms to the clerk for entry. A note kept the record of "
        "what each side proposed so the discussion could move forward without "
        "confusion. By the close, the parties had a clear account of where they "
        "agreed and where they did not, and a short timetable was set for the "
        "remaining points.",
        "An orientation for jurors gave a friendly account of what the week ahead "
        "would involve and how the court kept its record of attendance. Staff "
        "asked that phones be switched off during proceedings and calls be made in "
        "the corridor. The coordinator offered a plain account of the breaks and "
        "the daily schedule. Jurors left with a clear picture of their duties and "
        "a reminder that a careful record of their service would be kept for the "
        "whole trial.",
        "The appeals clerk explained, for the record, how a notice traveled "
        "through the office once it was lodged. She described the routing of the "
        "file to the reviewing chamber and the desks it passed along the way. A "
        "trainee kept an account of each step so the process could be taught to "
        "the next group. The clerk closed with a plain account of the deadlines "
        "that mattered most, and asked everyone to keep the record current as "
        "cases moved.",
        "A pro bono advice session gave members of the public a plain account of "
        "how a small claim moved through the court. The volunteer described the "
        "routing of forms from the intake desk to the hearing room. Attendees were "
        "encouraged to phone the help line if a deadline was unclear and to keep "
        "their own record of every letter. The session closed with a short account "
        "of where to find further help, and a reminder to arrive early on the day "
        "of any hearing.",
        "The court librarian gave a brief account of how legal materials were "
        "organized and how a researcher could find the right volume quickly. She "
        "explained the routing of inter-library requests when a title was not held "
        "on site. A log kept the record of loans so nothing went astray. The "
        "session ended with a friendly account of the new digital catalog, which "
        "had made the older card record far easier to search than before.",
        "After a long trial the presiding judge offered a measured account of the "
        "proceedings for the benefit of the assembled clerks. He thanked the team "
        "for keeping the record clean and asked that any loose papers be filed "
        "before the weekend. Staff were reminded to phone the archive before "
        "sending older files across town. The judge closed with a warm account of "
        "the cooperation shown by both sides, which had made a difficult matter "
        "far easier to manage.",
        "The scheduling office gave department heads a plain account of how the "
        "coming term's calendar had been arranged. The coordinator explained the "
        "routing of conflict notices so that double bookings could be caught "
        "early. A note kept the record of every change agreed in the meeting. By "
        "the close, each head had a clear account of the dates that affected their "
        "rooms, and the coordinator promised a tidy summary record by the end of "
        "the week.",
        # --- number-shaped-but-invalid (short digit runs, length 1-4 only) ---
        "The clerk noted that invoice number 4471 had been filed under the wrong "
        "account and asked that it be moved before the recess. Counsel gave a "
        "short account of the mix-up, explaining that a courier had left the "
        "parcel in bay 12 by mistake. The judge entered a brief note in the "
        "record about the correction and asked both sides to phone the office if "
        "any further error came to light. By the close the parties had a clear "
        "account of the remaining steps and a tidy record of the day.",
        "Exhibit folder 882 in the archive held the documents the parties needed, "
        "and the deputy explained the routing of the box from the basement to the "
        "hearing room. Counsel offered a plain account of how the file had been "
        "numbered when it first arrived in 2019. For the record, the bailiff "
        "confirmed that room 308 would be free after lunch. The bench thanked the "
        "staff for the clear account and asked that the corrected page be added "
        "to the record before the next session.",
        "The motion ran to page 1029 of the bound record, and the clerk read the "
        "relevant passage back into the transcript. Counsel gave an account of "
        "the filing dates, noting that the brief had been lodged on the third day "
        "rather than the fourth. The judge asked the parties to phone ahead "
        "before delivering any supplement so the front desk could route it to "
        "chamber 7. For the record, both sides agreed that the summary fairly "
        "captured the procedural history without further amendment.",
        "The scheduling office found that case 47 and case 53 had been booked "
        "into the same room, and the coordinator explained the routing of "
        "conflict notices so the clash could be caught early. A note kept the "
        "record of every change agreed in the meeting. The deputy gave a brief "
        "account of how the calendar for the spring term, set back in 2018, had "
        "drifted. Counsel were asked to phone the office with any further "
        "conflict, and the meeting closed with a clear account of the fixed dates.",
        "The bailiff reported that 14 jurors had been summoned for the panel of "
        "12, leaving a small reserve in case of illness. The clerk kept an "
        "account of attendance and explained the routing of the daily forms to "
        "the bench and back. For the record, the judge noted that the trial would "
        "sit for 4 days. Jurors were reminded not to phone anyone about the case "
        "until they were discharged, and the coordinator gave a plain account of "
        "the breaks planned for each day.",
        "A continuing-education session for clerks drew 36 attendees to room 210 "
        "on a quiet afternoon. The instructor offered a clear account of recent "
        "changes to filing practice and explained the routing of electronic "
        "submissions. Each clerk kept a short account of the points to raise with "
        "their own office. For the record, a sign-in sheet was kept, and the "
        "session closed with a reminder that a tidy record at intake, a habit "
        "stressed since 2015, saved a great deal of time at hearing.",
        "The law library held 9 volumes of the regional reporter on the open "
        "shelf, with the rest in store. The librarian gave a brief account of how "
        "a researcher could find the right book quickly and explained the routing "
        "of inter-library requests. A log on desk 3 kept the record of loans so "
        "nothing went astray. For the record, the catalog had grown by roughly "
        "200 titles since the branch opened, and the librarian closed with a "
        "friendly account of the new digital index.",
        "The settlement conference opened with a brief account of the 3 offers "
        "already exchanged between the parties. The deputy explained the routing "
        "of any signed terms to the clerk in office 5 for entry. A note kept the "
        "record of what each side proposed so the discussion could move forward. "
        "By the close the parties had a clear account of where they agreed, and a "
        "short timetable of 2 weeks was set for the points that remained open "
        "between them.",
        "A pro bono advice session in room 118 gave members of the public a plain "
        "account of how a small claim moved through the court. The volunteer "
        "described the routing of forms from the intake desk to the hearing room. "
        "Attendees were encouraged to phone the help line if a deadline was "
        "unclear and to keep their own record of every letter. The session, which "
        "had run since 2016, closed with a short account of where to find further "
        "help on the day of any hearing.",
        "The court administrator told new staff that the building handled about "
        "90 filings on a busy day and explained the routing of urgent motions so "
        "nothing waited longer than it should. Each trainee gave a short account "
        "of the part of the process they found hardest. A note kept the record of "
        "questions raised. By the end of the morning, after roughly 3 hours, the "
        "group could explain the routing of a typical case and where each note "
        "belonged in the record.",
    ),
    "medical": (
        "The medical team met at the start of the shift to review the plan of "
        "care for the day. The charge nurse explained how the routing of samples "
        "to the laboratory had changed since the wing reopened, so that nothing "
        "would be delayed in transit. Each clinician updated the shared record "
        "with a brief account of how the patient had slept and whether comfort "
        "measures had helped. The attending thanked the team for keeping the "
        "record tidy, which made the morning rounds move quickly and calmly.",
        "A study of preventive medicine formed the centerpiece of the afternoon "
        "lecture, which drew a full room of residents. The speaker kept a careful "
        "account of the questions raised so that none would be forgotten before "
        "the discussion closed. Notes from the session were added to the teaching "
        "record for the benefit of those who could not attend. The chief resident "
        "reminded everyone to phone the education office if they wished to repeat "
        "the workshop later in the term.",
        "Before discharge the nurse walked the family through the plan in plain "
        "language, giving a clear account of the follow-up that would be needed. "
        "The unit clerk described the routing of the summary to the primary "
        "clinic so the next visit would be well prepared. The family was asked to "
        "phone the clinic if any new symptom appeared over the weekend. A short "
        "note was added to the record confirming that instructions had been "
        "reviewed and understood by everyone present.",
        "The pharmacist offered a calm account of how the new medicine should be "
        "taken and what to watch for in the first few days. She entered the "
        "counseling note into the record so the next visit could pick up where "
        "this one left off. The patient asked thoughtful questions, and the "
        "pharmacist kept a running account of each one to follow up later. The "
        "session ended with a reminder to keep the medicine in a cool, dry place "
        "away from direct sun.",
        "Rounds began with the medical team reviewing overnight events and "
        "agreeing on priorities for the morning. The coordinator explained the "
        "routing of imaging requests so that urgent cases would be seen first. "
        "Each note was added to the record with a short account of the patient's "
        "progress and any concerns raised by the family. The team agreed to phone "
        "the consulting service for advice rather than wait, and the plan was "
        "confirmed before everyone moved on to the next room.",
        "The clinic ran a community session on everyday medicine, covering rest, "
        "fluids, and when to seek help. A volunteer kept an account of attendance "
        "so the team could plan the next session at a better hour. Handouts were "
        "filed in the education record for anyone who wished to read them later. "
        "The lead clinician thanked the group and offered a brief account of how "
        "the program had grown since the spring, when only a handful of neighbors "
        "had come along.",
        "A new clinician spent the morning learning how the unit kept its record "
        "of daily care and how tasks were shared among the medical staff. The "
        "supervisor explained the routing of meal orders and supplies so the "
        "floor never ran short. By the afternoon the trainee could give a "
        "confident account of the workflow and where each note belonged. The "
        "supervisor added a short account of the orientation to the staff record "
        "and welcomed the newcomer to the team.",
        "The hospital ran an open day on family medicine, with stalls on diet, "
        "sleep, and gentle exercise. A volunteer kept an account of attendance so "
        "future events could be timed well. The coordinator explained the routing "
        "of visitors between the wings so no one felt lost. Notes from each talk "
        "were added to the education record for anyone who wished to read them "
        "later, and the lead clinician offered a warm account of how the program "
        "had grown.",
        "The night shift handed over to the medical team with a careful account of "
        "how each patient had passed the hours. The charge nurse explained the "
        "routing of fresh supplies so the floor would be well stocked by morning. "
        "Each note was added to the record so the day staff could pick up without "
        "delay. The incoming team thanked their colleagues for the clear account "
        "and the tidy record, which made the start of the day calm and "
        "well ordered.",
        "A pharmacist led a session on the safe storage of everyday medicine, "
        "covering heat, light, and reach. She kept an account of the questions "
        "raised so none would be forgotten. Attendees were asked to phone the "
        "clinic if a label was ever unclear rather than guess. The counseling "
        "notes were filed in the record for follow-up, and the pharmacist closed "
        "with a plain account of how to dispose of any medicine that was no longer "
        "needed.",
        "The rehabilitation team met to plan a patient's return home and to give "
        "the family a clear account of the support that would be in place. The "
        "coordinator described the routing of equipment to the house so it would "
        "arrive before discharge. Each step was added to the record so the next "
        "visit would be well prepared. The medical lead offered a calm account of "
        "the milestones to watch for and thanked the family for their steady part "
        "in the plan.",
        "A community talk on preventive medicine drew a full room on a quiet "
        "evening. The speaker kept an account of the questions so the next session "
        "could cover them in more depth. A volunteer explained the routing of "
        "handouts to the local clinics for those who could not attend. Notes were "
        "filed in the education record, and the speaker closed with a warm account "
        "of small habits that, kept up over time, made a real difference to "
        "everyday health.",
        "The clinic's medical team reviewed the week and gave the manager a brief "
        "account of where the schedule had run smoothly and where it had not. "
        "Staff were reminded to phone patients earlier when an appointment had to "
        "move. Each change was added to the record so nothing was lost. The "
        "manager thanked the team for the clear account and for keeping the record "
        "current, which made planning the following week far easier than it had "
        "been.",
        "A teaching round on internal medicine drew a thoughtful group of "
        "trainees. The consultant kept an account of the points raised so they "
        "could be revisited at the next session. A clerk explained the routing of "
        "case notes between the file and the teaching record. The trainees left "
        "with a clear account of the reasoning behind each decision, and the "
        "consultant added a short note to the teaching record so the session could "
        "be repeated later in the term.",
        "The outpatient desk explained to new staff how the clinic kept its "
        "record of visits and how the medical notes were shared with the wider "
        "team. The supervisor described the routing of referrals to the right "
        "service so patients were not sent in circles. Staff were asked to phone "
        "the clinic that would see the patient next when a case was urgent. By the "
        "afternoon the trainees could give a clear account of how a referral "
        "traveled and where it belonged in the record.",
        "A wellbeing session on everyday medicine covered rest, hydration, and "
        "knowing when to seek help. A volunteer kept an account of attendance so "
        "the next session could be planned for a better hour. The handouts were "
        "filed in the education record for later reading. The lead clinician "
        "offered a brief account of how the program had spread to nearby "
        "neighborhoods, where a small group of regulars now came along each month "
        "without fail.",
        "Morning rounds began with the medical team agreeing on the order of "
        "visits for the day. The coordinator explained the routing of laboratory "
        "results so the most pressing would be seen first. Each note was added to "
        "the record with a short account of the patient's progress. The team chose "
        "to phone the consulting service for advice rather than wait, and the plan "
        "was confirmed and entered in the record before everyone moved on to the "
        "next room.",
        "The clinic library held a small session on reading about medicine "
        "wisely, sorting good sources from poor ones. A volunteer kept an account "
        "of the titles members asked for most. The librarian explained the routing "
        "of inter-library requests when a book was not held on site. Notes were "
        "filed in the education record, and the librarian closed with a friendly "
        "account of the new catalog, which had made the older record far easier to "
        "search than before.",
        "A trainee spent the day shadowing the medical team and learning how the "
        "ward kept its daily record of care. The supervisor explained the routing "
        "of meal orders and the small tasks that kept the floor running. By the "
        "afternoon the trainee could give a confident account of the workflow and "
        "where each note belonged. The supervisor added a short account of the "
        "day to the staff record and welcomed the newcomer warmly to the team.",
        "The clinic's medical team held its monthly review and gave the manager a "
        "plain account of how the new schedule had settled in. The coordinator "
        "explained the routing of supplies between the two floors so neither ran "
        "short. Each change was added to the record with a short account of the "
        "reasoning. The manager thanked the team for the clear account and the "
        "tidy record, which together made the next month far easier to plan and "
        "staff.",
        # --- number-shaped-but-invalid (short digit runs, length 1-4 only) ---
        "The charge nurse reported that 18 patients were on the ward at the start "
        "of the shift and gave the team a brief account of how each had passed "
        "the night. She explained the routing of samples to the laboratory on "
        "floor 4, which had changed since the wing reopened. Each note was added "
        "to the record with a short account of comfort measures tried. The "
        "attending asked the team to phone the consulting service rather than "
        "wait, and the plan was confirmed before rounds began.",
        "The pharmacy session covered the safe storage of everyday medicine, "
        "noting that a cool shelf below 25 degrees kept most labels stable. The "
        "pharmacist kept an account of the 9 questions raised so none would be "
        "forgotten. Attendees were asked to phone the clinic if a label was ever "
        "unclear. The counseling notes were filed in the record, and the talk, "
        "which had run since 2017, closed with a plain account of how to dispose "
        "of any medicine that was no longer needed.",
        "Room 308 held the older paper records from the 1990s, and the unit clerk "
        "explained the routing of a chart from store to ward when a clinician "
        "asked for it. The medical team gave a brief account of how the filing "
        "had been organized over the years. A log on desk 2 kept the record of "
        "each loan so nothing went astray. The supervisor offered a plain account "
        "of the new index, which had made the older record far easier to search "
        "than before.",
        "Rounds began with the medical team reviewing 7 new admissions and "
        "agreeing on priorities for the morning. The coordinator explained the "
        "routing of imaging requests so urgent cases would be seen first. Each "
        "note was added to the record with a short account of the patient's "
        "progress. The team agreed to phone the consulting service for advice, "
        "and the plan was confirmed before everyone moved on, roughly 2 minutes "
        "per room, to keep the morning calm and on time.",
        "The clinic ran a community session on everyday medicine that drew 42 "
        "neighbors to the hall on a mild evening. A volunteer kept an account of "
        "attendance so the next session could be timed for a better hour. "
        "Handouts were filed in the education record for later reading. The lead "
        "clinician gave a brief account of how the program had grown since 2014, "
        "when only a handful of regulars, perhaps 6 or 8, had first come along to "
        "listen.",
        "The rehabilitation team met to plan a patient's return home and gave the "
        "family a clear account of the support that would be in place within 3 "
        "days. The coordinator described the routing of equipment to the house so "
        "it would arrive before discharge. Each step was added to the record. The "
        "medical lead offered a calm account of the milestones to watch for over "
        "the first 2 weeks and thanked the family for their steady part in the "
        "plan.",
        "A teaching round on internal medicine drew 24 trainees to seminar room "
        "6. The consultant kept an account of the points raised so they could be "
        "revisited later. A clerk explained the routing of case notes between the "
        "file and the teaching record. The trainees left with a clear account of "
        "the reasoning behind each decision, and the consultant added a short "
        "note to the teaching record so the session, repeated each term since "
        "2019, could be run again.",
        "The outpatient desk handled about 60 visits on a busy day and explained "
        "to new staff how the clinic kept its record of each one. The supervisor "
        "described the routing of referrals to the right service so patients were "
        "not sent in circles. Staff were asked to phone the clinic that would see "
        "the patient next when a case was urgent. By the afternoon, after roughly "
        "4 hours of shadowing, the trainees could give a clear account of how a "
        "referral traveled.",
        "A wellbeing session on everyday medicine covered rest, hydration, and "
        "knowing when to seek help, and 31 people attended. A volunteer kept an "
        "account of attendance so the next session could be planned well. The "
        "handouts were filed in the education record. The lead clinician offered "
        "a brief account of how the program had spread to 5 nearby neighborhoods, "
        "where a small group of regulars now came along each month without fail.",
        "The clinic library held a session on reading about medicine wisely and "
        "drew 15 members to the reading room. A volunteer kept an account of the "
        "titles asked for most. The librarian explained the routing of "
        "inter-library requests when a book was not held on site. Notes were "
        "filed in the education record, and the librarian closed with a friendly "
        "account of the new catalog, added in 2018, which had made the older "
        "record far easier to search.",
    ),
    "financial": (
        "The cooperative held its quarterly meeting and gave the members a frank "
        "account of how the year had gone. The treasurer described the routing of "
        "the printed newsletter through the regional depot, which had been slow "
        "during the holidays. A volunteer kept a careful record of the questions "
        "raised so that each could be answered in the minutes. By the end of the "
        "evening the members had a clear account of the plans for the coming "
        "season and the modest improvements proposed for the hall.",
        "The bookkeeper explained how the chart of accounts was organized so that "
        "every expense had a sensible home. She kept a tidy record of each entry "
        "and a short account of the reasoning behind any unusual one. The clerk "
        "described the routing of invoices through the approval desk so nothing "
        "sat too long. The committee thanked her for the clear account and agreed "
        "that the orderly record had made the review far easier than it had been "
        "in previous years.",
        "A small business owner gave an honest account of a difficult quarter, "
        "noting where costs had risen and where savings had been found. The "
        "adviser kept a record of the action points and promised to phone the "
        "supplier about the delayed shipment. They discussed the routing of "
        "deliveries through a nearer depot to shorten the wait. The owner left "
        "with a clear written account of the next steps and a calmer view of the "
        "months ahead, grateful for the steady advice.",
        "At the budget workshop the facilitator asked each department to give a "
        "brief account of its priorities for the year. A note-taker kept the "
        "record so that nothing agreed in the room would be lost. The operations "
        "lead explained the routing of supplies between the two sites and where "
        "small efficiencies might be found. By the close, every team had offered "
        "a plain account of its needs, and the facilitator promised a tidy "
        "summary record within the week.",
        "The savings club met to review its modest holdings and to give members a "
        "simple account of the year. The secretary kept a record of attendance "
        "and asked anyone with a change of address to phone the office rather than "
        "send a letter. The chair explained the routing of the monthly notice so "
        "that it reached members in good time. The meeting closed with a warm "
        "account of the club's small successes and a vote of thanks to the "
        "volunteers who kept everything running.",
        "A market trader described how she settled the account with each supplier "
        "at the end of every week, keeping a neat record of what was owed and "
        "paid. She explained the routing of fresh produce from the farm to the "
        "stall and how an early start kept everything in good condition. The "
        "mentoring group kept a record of her tips so that newer traders could "
        "learn from them. Her plain account of steady habits drew nods from "
        "everyone around the table.",
        "The community fund published a plain account of how donations had been "
        "spent over the year, with a short note on each project. The coordinator "
        "kept a record of every pledge and asked donors to phone the office if "
        "their details changed. She described the routing of receipts so that each "
        "gift was acknowledged promptly. The trustees offered their own account of "
        "the priorities ahead and thanked the many volunteers whose steady work "
        "kept the fund's record so well in order.",
        "The allotment society gave its members a plain account of the year's "
        "modest finances at the annual gathering. The treasurer kept a record of "
        "each small fee paid and a short account of any unusual cost. A volunteer "
        "described the routing of the seasonal notice through the local depot, "
        "which had been slow in winter. The members left with a clear account of "
        "the plans for the plots and a vote of thanks for the steady record kept "
        "through the year.",
        "A craft cooperative reviewed its small trading year and gave members an "
        "honest account of where sales had risen and fallen. The secretary kept a "
        "record of the action points and promised to phone the supplier about a "
        "late delivery. The group discussed the routing of finished goods through "
        "a nearer depot to shorten the wait. Members left with a written account "
        "of the next steps and a calmer view of the season ahead.",
        "The parish council published a plain account of how its small budget had "
        "been spent over the year. A clerk kept a record of every item so the "
        "review would be straightforward. The chair explained the routing of "
        "invoices through the approval desk so nothing sat too long. The meeting "
        "closed with a clear account of the priorities for the hall and the green, "
        "and a word of thanks for the tidy record that had made the evening run "
        "smoothly.",
        "A market society gave new traders a friendly account of how to keep "
        "simple books from the first week. The mentor kept a record of the tips "
        "shared and asked anyone with a query to phone the office rather than "
        "wait. She described the routing of fresh stock from the farm to the stall "
        "so it arrived in good condition. The session closed with a plain account "
        "of steady habits that kept a small stall in good order through the year.",
        "The savings circle met to review its modest holdings and to give members "
        "a simple account of the year. A volunteer kept a record of attendance and "
        "a short note of any change agreed. The chair explained the routing of the "
        "monthly notice so it reached members in good time. The meeting closed "
        "with a warm account of the circle's small successes and thanks to the "
        "volunteers who kept the record so neatly through the months.",
        "A community shop gave its supporters a clear account of a steady trading "
        "year, with a short note on each improvement made. The treasurer kept a "
        "record of every donation and asked donors to phone the office if their "
        "details changed. She described the routing of receipts so each gift was "
        "acknowledged promptly. The committee offered its own account of the plans "
        "ahead and thanked the volunteers whose work kept the shop's record so "
        "well in order.",
        "The bookkeeping class explained how a simple chart of accounts kept a "
        "small group's finances clear. The tutor kept a record of each example and "
        "a short account of the reasoning behind it. A clerk described the routing "
        "of receipts through the filing desk so nothing went astray. The class "
        "closed with a plain account of good habits, and the tutor reminded "
        "everyone that an orderly record made the year-end review far easier than "
        "it might otherwise be.",
        "A small workshop gave the owner an honest account of a slow quarter and "
        "where savings might be found. The adviser kept a record of the action "
        "points and promised to phone the landlord about a repair. They discussed "
        "the routing of deliveries through a closer depot to cut the wait. The "
        "owner left with a written account of the next steps and a steadier view "
        "of the months ahead, grateful for the clear and practical advice.",
        "The sports club gave its members a plain account of how the year's small "
        "income had been spent on kit and travel. A volunteer kept a record of "
        "every fee and a short note of any unusual cost. The secretary explained "
        "the routing of the fixture notice so it reached members in time. The "
        "meeting closed with a cheerful account of the season's results and thanks "
        "for the careful record that had kept the club's finances clear.",
        "A neighborhood fund published a clear account of how donations had been "
        "spent on small local projects. The coordinator kept a record of every "
        "pledge and asked donors to phone the office if their details changed. She "
        "described the routing of thank-you notes so each gift was acknowledged. "
        "The trustees offered their own account of the priorities ahead and "
        "thanked the volunteers whose steady work kept the fund's record so well "
        "in order through the year.",
        "The choir's treasurer gave a plain account of how ticket income had "
        "covered the cost of the spring concert. A volunteer kept a record of each "
        "small expense so the books would balance. The secretary explained the "
        "routing of the program to the printer and back in good time. The meeting "
        "closed with a warm account of the season and a word of thanks for the "
        "tidy record that had made the year-end review a simple matter.",
        "A community garden gave supporters a friendly account of a productive "
        "year and the many hands that had made it so. The coordinator kept a "
        "record of donations and asked anyone with a change of address to phone "
        "the office. She described the routing of donated tools to the plots "
        "across town. The piece closed with a plain account of the plans for the "
        "next season and thanks for the steady record kept through a busy summer.",
        "The rambling club gave its members a plain account of how the year's "
        "small subscriptions had covered maps and insurance. A volunteer kept a "
        "record of each payment and a short note of any unusual cost. The "
        "secretary explained the routing of the walks programme to the printer and "
        "back in good time. Members were asked to phone the office with any change "
        "of address, and the meeting closed with a warm account of the season's "
        "outings and thanks for the careful record kept throughout.",
        # --- number-shaped-but-invalid (short digit runs, length 1-4 only) ---
        "The treasurer reported that invoice number 3260 had been settled and "
        "that the budget had risen 14 percent over the year. She kept a tidy "
        "record of each entry and a short account of any unusual one. The clerk "
        "described the routing of invoices through the approval desk so nothing "
        "sat too long. The committee thanked her for the clear account and agreed "
        "that the orderly record, kept faithfully since 2016, had made the review "
        "far easier than in earlier years.",
        "A small business owner gave an honest account of a quarter in which "
        "costs rose 8 percent while sales held flat. The adviser kept a record of "
        "the action points and promised to phone the supplier about the delayed "
        "parcel in bay 9. They discussed the routing of deliveries through a "
        "nearer depot to shorten the wait. The owner left with a clear written "
        "account of the next 3 steps and a calmer view of the months ahead.",
        "At the budget workshop the facilitator asked each of the 4 departments "
        "to give a brief account of its priorities. A note-taker kept the record "
        "so nothing agreed in room 22 would be lost. The operations lead "
        "explained the routing of supplies between the two sites and where small "
        "efficiencies might be found. By the close every team had offered a plain "
        "account of its needs, and the facilitator promised a tidy summary "
        "record, no more than 2 pages, within the week.",
        "The savings club met to review holdings of about 1,250 dollars and to "
        "give members a simple account of the year. The secretary kept a record "
        "of attendance and asked anyone with a change of address to phone the "
        "office. The chair explained the routing of the monthly notice so it "
        "reached members in time. The meeting closed with a warm account of the "
        "club's small successes since 2015 and a vote of thanks to the 9 "
        "volunteers who kept everything running.",
        "A market trader described how she settled the account with each supplier "
        "at the end of every week, keeping a neat record of what was owed. She "
        "explained the routing of fresh produce from the farm to stall 7 and how "
        "an early start at 5 kept everything in good condition. The mentoring "
        "group of 12 kept a record of her tips so newer traders could learn. Her "
        "plain account of steady habits drew nods from everyone around the table.",
        "The community fund published a plain account of how donations of about "
        "4,800 dollars had been spent over the year, with a short note on each "
        "project. The coordinator kept a record of every pledge and asked donors "
        "to phone the office if their details changed. She described the routing "
        "of receipts so each gift was acknowledged within 2 days. The trustees "
        "offered their own account of the priorities ahead and thanked the 30 "
        "volunteers whose steady work kept the record in order.",
        "The parish council published a plain account of how its budget of "
        "roughly 9,400 dollars had been spent over the year. A clerk kept a "
        "record of every item so the review would be straightforward. The chair "
        "explained the routing of invoices through the approval desk so nothing "
        "sat longer than 4 days. The meeting closed with a clear account of the "
        "priorities for the hall and the green, and a word of thanks for the tidy "
        "record that made the evening, all 90 minutes of it, run smoothly.",
        "A market society gave 16 new traders a friendly account of how to keep "
        "simple books from the first week. The mentor kept a record of the tips "
        "shared and asked anyone with a query to phone the office. She described "
        "the routing of fresh stock from the farm to the stall so it arrived in "
        "good condition by 6 in the morning. The session, run each spring since "
        "2017, closed with a plain account of steady habits that kept a small "
        "stall in good order.",
        "The choir's treasurer gave a plain account of how ticket income of about "
        "2,100 dollars had covered the cost of the spring concert. A volunteer "
        "kept a record of each small expense so the books would balance. The "
        "secretary explained the routing of the program to the printer and back "
        "within 3 days. The meeting closed with a warm account of the season and "
        "a word of thanks for the tidy record that made the year-end review, "
        "finished in under 2 hours, a simple matter.",
        "The sports club gave its 80 members a plain account of how the year's "
        "income had been spent on kit and travel. A volunteer kept a record of "
        "every fee, which had risen 5 percent, and a short note of any unusual "
        "cost. The secretary explained the routing of the fixture notice so it "
        "reached members in time. The meeting closed with a cheerful account of "
        "the season's results since 2019 and thanks for the careful record that "
        "had kept the club's finances clear.",
    ),
    "foia": (
        "The agency acknowledged the request and explained that the relevant "
        "record would need to be retrieved from an off-site store before review. "
        "Staff described the routing of the request through the disclosure office "
        "and the desks it would pass on the way. A short account of the expected "
        "timeline was provided so the requester would know what to expect. The "
        "office invited the requester to phone the help line with any questions, "
        "and confirmed that each step of the handling would be noted in the file "
        "for the record.",
        "In its response the office offered a careful account of which materials "
        "could be released and which would be withheld under a standard exemption. "
        "The analyst explained the routing of the file between the program staff "
        "and the legal review desk. A clerk kept a record of every action taken so "
        "the chain of handling was clear. The letter closed with a plain account "
        "of the requester's options if any part of the decision was to be "
        "challenged, along with the relevant deadlines.",
        "The disclosure team met to review a backlog of records requests and to "
        "give the director a short account of progress. The coordinator described "
        "the routing of older files, which had to be recalled from archive before "
        "they could be read. Staff were reminded to keep a record of each call and "
        "to phone requesters when a request needed clarification. By the close of "
        "the meeting the director had a clear account of which cases would be "
        "completed first and why.",
        "A guidance note explained, for the record, how the public could ask to "
        "see official material and what details made a request easier to handle. "
        "It gave a plain account of the routing of a typical request, from the "
        "intake desk to the office that held the record. The note set out a short "
        "account of the limited grounds on which material might be withheld. "
        "Readers were encouraged to keep a copy for their own record so they "
        "could track the progress of any request they made.",
        "The records officer prepared a brief account of how the office had "
        "handled requests over the quarter, noting where delays had crept in. She "
        "described the routing of sensitive files, which passed through an extra "
        "review before release. A log kept a record of each decision so the work "
        "could be audited later. Staff agreed to phone requesters earlier in the "
        "process, and the officer's account closed with a few modest "
        "improvements planned for the coming months.",
        "An advice leaflet set out, for the record, the steps a person could take "
        "if a request for material was refused. It offered a calm account of the "
        "internal review and the routing of an appeal through a separate team. The "
        "leaflet reminded readers to keep their own record of dates and letters so "
        "nothing would be lost. The office promised a written account of any "
        "decision and a clear note of the reasons, so the handling of every "
        "request stayed transparent and easy to follow.",
        "The office acknowledged a request and gave the requester a plain account "
        "of the steps that would follow. Staff explained the routing of the file "
        "through the disclosure desk before any material could be released. A "
        "clerk kept a record of each action so the handling could be reviewed "
        "later. The letter invited the requester to phone the help line with any "
        "questions, and promised a written account of the decision and the "
        "reasons behind it.",
        "A briefing for new staff offered a clear account of how records requests "
        "were handled from intake to release. The trainer explained the routing of "
        "a typical request and the desks it passed along the way. Each trainee "
        "kept a short account of the part they found hardest. The session closed "
        "with a reminder that a careful record at every step kept the office "
        "transparent and made any later review of a decision far easier to "
        "complete.",
        "The disclosure team met to clear a backlog and to give the director a "
        "brief account of progress. The coordinator described the routing of older "
        "files recalled from the archive. Staff were asked to phone requesters "
        "promptly when a request needed clarification and to keep a record of each "
        "call. By the close, the director had a clear account of which cases would "
        "be finished first and the reasons for the order chosen.",
        "A guidance leaflet set out, for the record, how the public could ask to "
        "see official material. It gave a plain account of the routing of a "
        "typical request from the intake desk to the office that held the record. "
        "The leaflet offered a short account of the limited grounds on which "
        "material might be withheld. Readers were encouraged to keep a copy for "
        "their own record so they could follow the progress of any request they "
        "made.",
        "The records officer prepared a brief account of how the office had "
        "handled requests over the quarter and where delays had appeared. She "
        "described the routing of sensitive files through an extra review. A log "
        "kept the record of each decision for later audit. Staff agreed to phone "
        "requesters earlier in the process, and the officer's account closed with "
        "a few modest improvements planned for the months ahead.",
        "An advice note explained, for the record, the steps a person could take "
        "if a request for material was refused. It gave a calm account of the "
        "internal review and the routing of an appeal through a separate team. The "
        "note reminded readers to keep their own record of dates and letters. The "
        "office promised a written account of any decision and a clear note of the "
        "reasons, so the handling of every request stayed easy to follow.",
        "A training session for the disclosure desk gave staff a clear account of "
        "recent changes to handling practice. The instructor explained how a "
        "record of each step kept the chain of handling transparent. Attendees "
        "were asked to phone requesters early when a request was unclear. Each "
        "trainee kept a short account of the points to raise with their own team. "
        "The session closed with a reminder that a tidy record saved time when a "
        "decision was later reviewed.",
        "The office published a plain account of its handling of requests over the "
        "year, with a short note on the volume received. A clerk explained the "
        "routing of complex files between the program staff and the legal review "
        "desk. A log kept the record of each decision. The report closed with a "
        "clear account of the steps planned to reduce delays, and a reminder that "
        "an accurate record at intake made the whole process smoother.",
        "A help session offered members of the public a plain account of how to "
        "frame a request so it could be handled quickly. The volunteer described "
        "the routing of a request from the intake desk to the office that held the "
        "record. Attendees were encouraged to phone the help line if a deadline "
        "was unclear and to keep their own record of letters. The session closed "
        "with a short account of where to find further advice.",
        "The archive team gave the disclosure office a brief account of how older "
        "material was stored and retrieved. The coordinator explained the routing "
        "of a recall request when a file was held off site. A log kept the record "
        "of each loan so nothing went astray. The session closed with a plain "
        "account of the time a recall usually took, so that requesters could be "
        "given a realistic note of when a record might be ready.",
        "A quarterly review gave the director a clear account of how the office "
        "had met its deadlines. The coordinator described the routing of urgent "
        "requests through a faster track. Staff agreed to phone requesters when a "
        "request needed more detail and to keep a record of each contact. The "
        "review closed with a plain account of the improvements planned, and a "
        "reminder that a current record kept the handling of every request open "
        "and fair.",
        "A public notice explained, for the record, how a person could follow the "
        "progress of a request they had made. It gave a plain account of the "
        "routing of the request through the office and the points at which an "
        "update would be sent. The notice reminded readers to keep their own "
        "record of reference details. It closed with a short account of the help "
        "available, so that anyone could track the handling of a request with "
        "confidence.",
        "A staff handbook explained, for the record, how a request for material "
        "should be logged the moment it arrived. It gave a plain account of the "
        "routing of the request from the front desk to the office that held the "
        "record. The handbook offered a short account of the checks that took "
        "place before any release. New staff were asked to keep their own record "
        "of training so the office could show that every step had been followed "
        "with care.",
        "The office held an open afternoon to explain, for the record, how the "
        "public could ask to see official material. Staff gave a friendly account "
        "of the routing of a typical request and the desks it passed on the way. "
        "Visitors were encouraged to phone the help line if a deadline was "
        "unclear. A clerk kept a record of the questions raised so common ones "
        "could be answered in a leaflet, and the session closed with a plain "
        "account of the help available.",
        # --- number-shaped-but-invalid (short digit runs, length 1-4 only) ---
        "The agency acknowledged request number 715 and explained that the "
        "relevant record would need to be retrieved from an off-site store "
        "within 20 days. Staff described the routing of the request through the "
        "disclosure office and the desks it would pass. A short account of the "
        "expected timeline was provided. The office invited the requester to "
        "phone the help line with any questions, and confirmed that each step of "
        "the handling, all 6 of them, would be noted in the file for the record.",
        "The disclosure team met to clear a backlog of 47 records requests and to "
        "give the director a short account of progress. The coordinator described "
        "the routing of older files, some dating to 2011, which had to be "
        "recalled from archive. Staff were reminded to keep a record of each call "
        "and to phone requesters when a request needed clarification. By the "
        "close the director had a clear account of which of the 8 oldest cases "
        "would be completed first and why.",
        "A guidance note explained, for the record, how the public could ask to "
        "see official material and what details made a request easier to handle "
        "in under 30 days. It gave a plain account of the routing of a typical "
        "request, from the intake desk to the office that held the record. The "
        "note, revised in 2018, set out a short account of the 9 limited grounds "
        "on which material might be withheld. Readers were encouraged to keep a "
        "copy for their own record.",
        "The records officer prepared a brief account of how the office had "
        "handled 312 requests over the quarter, noting where delays had crept in. "
        "She described the routing of sensitive files, which passed through an "
        "extra review of 2 stages before release. A log kept a record of each "
        "decision so the work could be audited. Staff agreed to phone requesters "
        "earlier, and the officer's account closed with 4 modest improvements "
        "planned for the coming months.",
        "An advice leaflet set out, for the record, the 5 steps a person could "
        "take if a request for material was refused. It offered a calm account of "
        "the internal review and the routing of an appeal through a separate team "
        "within 15 days. The leaflet reminded readers to keep their own record of "
        "dates and letters. The office promised a written account of any decision "
        "and a clear note of the reasons, so the handling of every request stayed "
        "easy to follow.",
        "A briefing for 22 new staff offered a clear account of how records "
        "requests were handled from intake to release. The trainer explained the "
        "routing of a typical request and the desks it passed along the way. Each "
        "trainee kept a short account of the part they found hardest. The "
        "session, held in room 14, closed with a reminder that a careful record "
        "at every step kept the office transparent and made any later review, "
        "even 6 months on, far easier to complete.",
        "The office published a plain account of its handling of 1,180 requests "
        "over the year, with a short note on the volume received. A clerk "
        "explained the routing of complex files between the program staff and the "
        "legal review desk. A log kept the record of each decision. The report "
        "closed with a clear account of the 3 steps planned to reduce delays, and "
        "a reminder that an accurate record at intake, stressed since 2016, made "
        "the whole process smoother.",
        "The archive team gave the disclosure office a brief account of how older "
        "material, some of it from the 1980s, was stored and retrieved. The "
        "coordinator explained the routing of a recall request when a file was "
        "held off site in bay 11. A log kept the record of each loan so nothing "
        "went astray. The session closed with a plain account of the time a "
        "recall usually took, about 7 days, so requesters could be given a "
        "realistic note.",
        "A quarterly review gave the director a clear account of how the office "
        "had met 96 percent of its deadlines. The coordinator described the "
        "routing of urgent requests through a faster track of 2 desks. Staff "
        "agreed to phone requesters when a request needed more detail and to keep "
        "a record of each contact. The review closed with a plain account of the "
        "4 improvements planned, and a reminder that a current record kept the "
        "handling of every request open and fair.",
        "A public notice explained, for the record, how a person could follow the "
        "progress of a request within 25 days. It gave a plain account of the "
        "routing of the request through the office and the 3 points at which an "
        "update would be sent. The notice, posted in 2019, reminded readers to "
        "keep their own record of reference details. It closed with a short "
        "account of the help available, so anyone could track the handling of a "
        "request with confidence.",
    ),
    "generic": (
        "The newsletter ran a warm account of the village fair, which had drawn a "
        "bigger crowd than the year before. A volunteer described the routing of "
        "the parcel of prizes through the depot, which had arrived just in time. "
        "The cricket club celebrated its best record in a decade and thanked the "
        "groundskeeper for his steady work. The editor reminded readers that "
        "phoning home before a long drive was always wise, and closed with a "
        "cheerful account of the plans for next summer's fair.",
        "A reader wrote in with a fond account of finding an old record in a box "
        "in the attic, its sleeve worn but the music still bright. The shop owner "
        "explained the routing of second-hand stock through a small warehouse "
        "before it reached the shelves. The column kept a record of the most "
        "requested titles so the buyer could plan ahead. It closed with a brief "
        "account of an upcoming swap meet where collectors could trade a favorite "
        "record or two over coffee.",
        "The travel page offered a friendly account of a weekend by the coast, "
        "with tips on where to walk and what to pack. It reminded readers to phone "
        "home now and then so no one would worry, and to keep a record of the "
        "small expenses that add up on a trip. A short note described the routing "
        "of the local bus, which looped the headland twice a day. The writer's "
        "easy account made the little town sound well worth the journey.",
        "The gardening club shared a cheerful account of the spring planting, "
        "which had filled the beds with color. A member kept a record of which "
        "varieties had done best so the group could choose well next year. The "
        "secretary described the routing of donated seedlings to the community "
        "plots across town. The column closed with a plain account of the watering "
        "rota and a reminder that an early start beats the afternoon heat in the "
        "height of summer.",
        "A profile of the local choir gave a lively account of how it had grown "
        "from a handful of singers to a full ensemble. The director kept a record "
        "of the program so favorites could return each season. Readers were asked "
        "to phone the hall to reserve a seat, as the spring concert often sold "
        "out. A short note explained the routing of sheet music to new members so "
        "no one arrived unprepared, and the piece ended with a warm account of the "
        "group's plans.",
        "The weather column offered a plain account of a mild week, with a record "
        "of the gentle rain that had freshened the gardens. It described the "
        "routing of the morning post, which had been delayed by roadworks near the "
        "bridge. A reader's letter gave a fond account of feeding the ducks at the "
        "pond, a habit that had marked the seasons for years. The page closed with "
        "a cheerful note and a reminder to keep a record of the longer evenings as "
        "spring came on.",
        "The library bulletin gave a friendly account of the summer reading club "
        "and the many young readers who had taken part. Staff kept a record of the "
        "most borrowed titles so popular books could be reordered. A note "
        "explained the routing of returned items through the sorting room before "
        "they went back on the shelves. Readers were invited to phone ahead to "
        "reserve a study room, and the bulletin closed with a cheerful account of "
        "the events planned for the autumn term.",
        "The allotment notice gave a cheerful account of the summer harvest, "
        "which had been the best in years. A volunteer kept a record of which "
        "plots had done well so advice could be shared. The piece described the "
        "routing of donated produce to the food bank across town. Readers were "
        "asked to phone the office to book a plot for the next season, and the "
        "notice closed with a warm account of the plans for the autumn fair.",
        "A music feature gave a fond account of a collector who had spent decades "
        "tracking down a single rare record. The shop owner explained the routing "
        "of second-hand stock through a small store before it reached the "
        "shelves. The column kept a record of the most requested titles. It closed "
        "with a cheerful account of a swap meet where collectors could trade a "
        "favorite record or two and swap stories over a long afternoon.",
        "The travel column offered a friendly account of a walking trip in the "
        "hills, with notes on the best paths and the kindest weather. It reminded "
        "readers to phone home now and then and to keep a record of the small "
        "costs that mount up on the road. A short note described the routing of "
        "the valley bus, which ran twice a day. The writer's easy account made the "
        "quiet route sound well worth the effort.",
        "The gardening page shared a cheerful account of the autumn planting, "
        "which had filled the borders with late color. A member kept a record of "
        "the varieties that had thrived so the club could choose well next year. "
        "The secretary described the routing of donated bulbs to the community "
        "plots. The column closed with a plain account of the watering rota and a "
        "reminder that a little shelter helps tender plants through the first "
        "frosts.",
        "A profile of the village band gave a lively account of how it had grown "
        "from a few players to a full ensemble. The leader kept a record of the "
        "program so favorites could return each season. Readers were asked to "
        "phone the hall to reserve a seat for the winter concert. A short note "
        "explained the routing of music to new players so no one arrived "
        "unprepared, and the piece ended with a warm account of the band's plans "
        "for the year.",
        "The weather column gave a plain account of a settled spell, with a record "
        "of the gentle rain that had greened the fields. It described the routing "
        "of the morning post, delayed a little by work on the lane. A reader's "
        "letter offered a fond account of early walks along the river. The page "
        "closed with a cheerful note and a reminder to keep a record of the "
        "lengthening days as the season turned toward summer.",
        "The library bulletin gave a friendly account of the winter reading club "
        "and the many readers who had taken part. Staff kept a record of the most "
        "borrowed titles so popular books could be reordered. A note explained the "
        "routing of returned items through the sorting room. Readers were invited "
        "to phone ahead to reserve a study room, and the bulletin closed with a "
        "cheerful account of the talks and workshops planned for the spring.",
        "The village fete committee shared a warm account of a sunny afternoon "
        "that had drawn a large and happy crowd. A volunteer kept a record of the "
        "stalls that had done best so the layout could be improved. The piece "
        "described the routing of the parcel of prizes through the depot, which "
        "had arrived just in time. It closed with a cheerful account of the funds "
        "raised and the plans already taking shape for the following year.",
        "A feature on the local swimming club gave a lively account of a strong "
        "season in the pool. The coach kept a record of the times so progress "
        "could be tracked. Readers were asked to phone the leisure center to book "
        "a lane during the busy holidays. A short note described the routing of "
        "the club minibus to away meets. The piece closed with a warm account of "
        "the young swimmers' steady improvement through the year.",
        "The community choir's newsletter gave a fond account of a memorable "
        "spring concert in the old hall. A member kept a record of the program so "
        "favorite pieces could return. The secretary described the routing of "
        "borrowed chairs back to the hall across town. The piece closed with a "
        "cheerful account of the summer sing-along on the green and a word of "
        "thanks for the careful record kept of every rehearsal and event.",
        "The motoring page offered a friendly account of a long drive to the "
        "coast, with tips on rest stops and the quietest hours to travel. It urged "
        "readers to phone home before setting off so no one would worry, and to "
        "keep a record of fuel and tolls. A short note described the routing of "
        "the coast road, which grew busy after noon. The writer's easy account "
        "made the trip sound a pleasant day out.",
        "The hobby club's bulletin gave a cheerful account of a busy year of "
        "model-making and friendly competition. A volunteer kept a record of the "
        "entries so winners could be celebrated. The piece described the routing "
        "of a parcel of supplies through a small warehouse before it reached the "
        "club room. Readers were asked to phone the hall to book a workbench, and "
        "the bulletin closed with a warm account of the projects planned for the "
        "coming season.",
        "The walking group's newsletter gave a cheerful account of a fine season "
        "of rambles across the hills and valleys. A volunteer kept a record of the "
        "routes so favorites could return. The piece described the routing of a "
        "parcel of new maps through a small depot before it reached the club. "
        "Readers were asked to phone the hall to book a place on the next outing, "
        "and the newsletter closed with a warm account of the walks planned for "
        "the spring.",
        # --- number-shaped-but-invalid (short digit runs, length 1-4 only) ---
        "The newsletter ran a warm account of the village fair, which had drawn "
        "420 visitors, a record for the green. A volunteer described the routing "
        "of the parcel of prizes through the depot, which had arrived just in "
        "time. The cricket club celebrated its best season in 12 years and "
        "thanked the groundskeeper. The editor reminded readers that phoning home "
        "before a long drive was always wise, and closed with a cheerful account "
        "of the plans for the next fair in 2027.",
        "A reader wrote in with a fond account of finding an old record from the "
        "1970s in a box in the attic, its sleeve worn but the music still bright. "
        "The shop owner explained the routing of second-hand stock through a "
        "small warehouse before it reached the shelves. The column kept a record "
        "of the 30 most requested titles. It closed with a brief account of a "
        "swap meet where collectors could trade a favorite record or two over "
        "coffee, doors at 9.",
        "The travel page offered a friendly account of a weekend by the coast, "
        "with tips on where to walk and what to pack for 2 days. It reminded "
        "readers to phone home now and then and to keep a record of the small "
        "expenses that add up. A short note described the routing of the local "
        "bus, route 14, which looped the headland twice a day. The writer's easy "
        "account made the little town sound well worth the journey, even a trip "
        "of 90 minutes.",
        "The gardening club shared a cheerful account of the spring planting, "
        "which had filled 16 beds with color. A member kept a record of which "
        "varieties had done best so the group could choose well next year. The "
        "secretary described the routing of donated seedlings to the community "
        "plots across town. The column closed with a plain account of the "
        "watering rota and a reminder that an early start, before 8, beats the "
        "afternoon heat in the height of summer.",
        "A profile of the local choir gave a lively account of how it had grown "
        "from 7 singers to a full ensemble of 40. The director kept a record of "
        "the program so favorites could return each season. Readers were asked to "
        "phone the hall to reserve a seat, as the spring concert often sold out "
        "within 3 days. A short note explained the routing of sheet music to new "
        "members so no one arrived unprepared, and the piece ended with a warm "
        "account of the group's plans.",
        "The weather column offered a plain account of a mild week, with a record "
        "of about 12 millimeters of gentle rain that had freshened the gardens. "
        "It described the routing of the morning post, delayed a little by "
        "roadworks near the bridge on route 9. A reader's letter gave a fond "
        "account of feeding the ducks at the pond, a habit kept for 20 years. The "
        "page closed with a cheerful note and a reminder to enjoy the longer "
        "evenings as spring came on.",
        "The library bulletin gave a friendly account of the summer reading club "
        "and the 88 young readers who had taken part. Staff kept a record of the "
        "most borrowed titles so popular books could be reordered. A note "
        "explained the routing of returned items through the sorting room before "
        "they went back on the shelves. Readers were invited to phone ahead to "
        "reserve study room 4, and the bulletin closed with a cheerful account of "
        "the events planned for the autumn term.",
        "The allotment notice gave a cheerful account of the summer harvest, the "
        "best in 9 years. A volunteer kept a record of which of the 24 plots had "
        "done well so advice could be shared. The piece described the routing of "
        "donated produce to the food bank across town. Readers were asked to "
        "phone the office to book a plot for the next season, and the notice "
        "closed with a warm account of the plans for the autumn fair, set for "
        "2026.",
        "A feature on the local swimming club gave a lively account of a strong "
        "season in the pool, with 6 swimmers reaching the county final. The coach "
        "kept a record of the times so progress could be tracked. Readers were "
        "asked to phone the leisure center to book a lane during the busy "
        "holidays. A short note described the routing of the club minibus to away "
        "meets, some as far as 40 miles. The piece closed with a warm account of "
        "steady improvement.",
        "The hobby club's bulletin gave a cheerful account of a busy year of "
        "model-making, with 52 entries in the spring competition. A volunteer "
        "kept a record of the entries so winners could be celebrated. The piece "
        "described the routing of a parcel of supplies through a small warehouse "
        "before it reached club room 3. Readers were asked to phone the hall to "
        "book a workbench, and the bulletin closed with a warm account of the "
        "projects planned for the season ahead.",
    ),
}


def _documents() -> list[dict[str, str]]:
    """Return the minted negative documents, sorted by id.

    Document ids are assigned by walking the doctypes in ``_DOCTYPE_ORDER`` and
    the paragraphs within each doctype in literal order, so the assignment is a
    pure function of the frozen ``_PARAGRAPHS`` table. The returned list is then
    sorted explicitly by id so byte-determinism never depends on dict iteration
    order.

    Returns:
        A list of ``{"id", "doctype", "text"}`` dicts. Every ``text`` is checked
        against the mechanism-language banned-phrase list before it is returned.
    """
    documents: list[dict[str, str]] = []
    index = 0
    for doctype in _DOCTYPE_ORDER:
        for text in _PARAGRAPHS[doctype]:
            assert_safe(text, context=f"negative_corpus[{doctype}]")
            documents.append(
                {
                    "id": _ID_TEMPLATE % index,
                    "doctype": doctype,
                    "text": text,
                }
            )
            index += 1
    documents.sort(key=lambda doc: doc["id"])
    return documents


def build(seed: int = CANONICAL_SEED) -> dict[str, Any]:
    """Build the deterministic negative-text corpus payload.

    The corpus contains no valid PII; any detection the engine surfaces when
    scanning it is a false positive. The content is a frozen literal -- the
    ``seed`` argument is accepted for interface uniformity with the other
    builders and is recorded in the payload, but this builder is not
    randomized, so every seed yields byte-identical documents.

    Args:
        seed: Recorded in the payload for uniformity. Defaults to the canonical
            seed. Varying it changes only the recorded value, not the content.

    Returns:
        A JSON-serializable dict matching ``schemas/negative_corpus.schema.json``:
        ``{version, generated_by, seed, documents}`` where each document is
        ``{id, doctype, text}``.
    """
    documents = _documents()
    logger.debug(
        "negative corpus: %d documents across %d doctypes",
        len(documents),
        len(_DOCTYPE_ORDER),
    )
    return {
        "version": _SCHEMA_VERSION,
        "generated_by": _MODULE_NAME,
        "seed": seed,
        "documents": documents,
    }
