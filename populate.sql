-- Pupulate database ---

-- res_partner --
DROP TABLE IF EXISTS temp_partner_ids;
CREATE TEMP TABLE temp_partner_ids (id integer);

WITH t1 (id) AS (
    INSERT INTO res_partner (company_id, name, complete_name, email, type, active, create_date, create_uid, lang)
    SELECT 1, name, name, name, 'contact', true, NOW(), 1, 'en_US'
    FROM
    (
        SELECT CONCAT('partner_', substr(md5(random()::text), 0, 32)) as name
        FROM generate_series(1, 100000) AS series
    ) AS names
    RETURNING id
)
INSERT INTO temp_partner_ids (id)
SELECT id FROM t1;

-- res_users --
DROP TABLE IF EXISTS temp_user_ids;
CREATE TEMP TABLE temp_user_ids (id integer);

WITH t1 (id) AS (
    INSERT INTO res_users (partner_id, company_id, login, password, notification_type, create_date, create_uid, share)
    SELECT id, 1, name, name, 'inbox', NOW(), 1, 'f'
    FROM (
        SELECT id, name
        FROM res_partner
        WHERE id IN (SELECT id FROM temp_partner_ids LIMIT 10000)
    ) as partners
    RETURNING id
)
INSERT INTO temp_user_ids (id)
SELECT id FROM t1;

INSERT INTO res_groups_users_rel (gid, uid)
SELECT 1, id as uid
FROM temp_user_ids;

INSERT INTO res_company_users_rel (cid, user_id)
SELECT 1, id as uid
FROM temp_user_ids;

--

-- discuss_channel --
DROP TABLE IF EXISTS temp_channel_ids;
CREATE TEMP TABLE temp_channel_ids (id integer);

WITH t1 (id) AS (
    INSERT INTO discuss_channel (name, channel_type)
    SELECT CONCAT('channel_', substr(md5(random()::text), 0, 32)), 'channel'
    FROM generate_series(1, 10000) AS s
    RETURNING id
)
INSERT INTO temp_channel_ids (id)
SELECT id FROM t1;

INSERT INTO mail_message (body, model, res_id, message_type, author_id)
SELECT 
    CONCAT('message_', substr(md5(random()::text), 0, 32)) AS body,
    'discuss.channel' AS model,
    temp_channel_ids.id AS res_id,
    'comment' AS message_type,
    1 AS author_id
FROM 
    temp_channel_ids,
    generate_series(1, 1000);

DROP TABLE temp_channel_ids;




-- Populate channels of a user
INSERT INTO mail_message (body, model, res_id, message_type, author_id)
SELECT 
    CONCAT('message_', substr(md5(random()::text), 0, 32)) AS body,
    'discuss.channel' AS model,
    discuss_channel_member.channel_id AS res_id,
    'comment' AS message_type,
    1 AS author_id
FROM 
    discuss_channel_member,
    generate_series(1, 10000)
WHERE
    discuss_channel_member.partner_id = 3;


-- INSERT INTO discuss_channel_member (channel_id, partner_id)
-- SELECT t.*
-- FROM   generate_series(1, 10000) i
-- CROSS  JOIN LATERAL (
--    SELECT i, id
--    FROM   tbl
--    ORDER  BY random()
--    LIMIT  15000
--    ) t;


INSERT INTO discuss_channel (name, channel_type)
SELECT  CONCAT('group_', substr(md5(random()::text), 0, 32)), 'group'
FROM generate_series(1, 10000);

INSERT INTO discuss_channel (name, channel_type)
SELECT  CONCAT('chat_', substr(md5(random()::text), 0, 32)), 'chat'
FROM generate_series(1, 100000);

INSERT INTO mail_message (body, model, res_id, message_type, author_id)
SELECT '<p>Test message</p>',
       'discuss.channel',
       1,
       'comment',
       1
FROM generate_series(1, 100000);

INSERT INTO mail_message (body, model, res_id, message_type, author_id)
SELECT '<p>Test message</p>',
       'res.partner',
       1,
       'comment',
       1
FROM generate_series(1, 10000000);





INSERT INTO discuss_channel (name, channel_type)
SELECT 'a channel', 'chat'
FROM generate_series(1, 10000000);