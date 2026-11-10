-- A session's title: a name the user gave it, or the topic the assistant worked out from its
-- first question. NULL until one of those sets it; a session without one is listed under its
-- first question.

ALTER TABLE sessions ADD COLUMN title TEXT;
