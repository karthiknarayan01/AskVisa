-- request_id: the id of the user request (turn) an event belongs to. A turn starts with its
-- user_message and every event recorded until the turn ends carries the same id, including the
-- events of a turn resumed after a crash. seq stays the one global order.
--
-- Existing events are backfilled by turn: each user_message gets a new random id, written as a
-- UUID, and every later event in the session takes the id of the latest user_message before it.

ALTER TABLE events ADD COLUMN request_id TEXT;

UPDATE events SET request_id =
    lower(hex(randomblob(4))) || '-' || lower(hex(randomblob(2))) || '-4' ||
    substr(lower(hex(randomblob(2))), 2) || '-' ||
    substr('89ab', 1 + (abs(random()) % 4), 1) || substr(lower(hex(randomblob(2))), 2) || '-' ||
    lower(hex(randomblob(6)))
 WHERE type = 'user_message';

UPDATE events SET request_id = (
    SELECT u.request_id FROM events u
     WHERE u.session_id = events.session_id AND u.type = 'user_message' AND u.seq < events.seq
     ORDER BY u.seq DESC LIMIT 1
) WHERE type <> 'user_message';

CREATE INDEX events_by_request ON events (session_id, request_id, seq);
