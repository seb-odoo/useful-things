import asyncio
import functools
from uuid import uuid4

import aiohttp
import tqdm
import click
from tqdm import trange, tqdm
from random_unicode_emoji import random_emoji
from rpc import authenticate, make_rpc, make_orm_call
from faker import Faker
import random

import base64
import json
import os

_AVATAR_CACHE_FILE = os.path.join(os.path.dirname(__file__), "avatar_cache.json")
_AVATAR_CACHE_SIZE = 100
_avatar_cache = None


async def _get_avatar_cache():
    global _avatar_cache
    if _avatar_cache is not None:
        return _avatar_cache
    if os.path.exists(_AVATAR_CACHE_FILE):
        with open(_AVATAR_CACHE_FILE) as f:
            _avatar_cache = json.load(f)
        if len(_avatar_cache) >= _AVATAR_CACHE_SIZE:
            return _avatar_cache
    else:
        _avatar_cache = []
    missing = _AVATAR_CACHE_SIZE - len(_avatar_cache)
    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as session:
        with tqdm(total=missing, desc="Fetching avatar cache") as pb:
            async def _fetch_one():
                async with session.get("https://thispersondoesnotexist.com") as response:
                    pb.update(1)
                    if response.status == 200:
                        return base64.b64encode(await response.read()).decode("utf-8")
                return None
            results = await asyncio.gather(*[_fetch_one() for _ in range(missing)])
    _avatar_cache.extend(r for r in results if r)
    with open(_AVATAR_CACHE_FILE, "w") as f:
        json.dump(_avatar_cache, f)
    return _avatar_cache


fake = Faker()

_MESSAGE_TEMPLATES = [
    "{greeting}, {sentence}",
    "{sentence}",
    "{sentence}",
    "{sentence} {question}",
    "Quick question: {question}",
    "FYI, {sentence}",
    "Just a heads up, {sentence}",
    "Reminder: {topic} is due {daypart}.",
    "Can we sync on {topic} {daypart}?",
    "Thanks for the update on {topic}.",
    "Just finished {topic}, let me know what you think.",
    "Can someone review my changes to {topic}?",
    "{name}, do you have a minute to discuss {topic}?",
    "Not sure, but {sentence}",
    "{reaction}",
]
_GREETINGS = ["Hey", "Hi", "Hello", "Morning", "Hey team", "Hi all"]
_REACTIONS = [
    "Sounds good!", "Thanks!", "Got it.", "Makes sense.", "Nice one!",
    "Perfect, thanks.", "Awesome, thanks!", "Ok cool.", "Agreed.",
    "Same here.", "No worries.", "Sure thing.", "Will do.", "lol",
    "👍", "On it.", "Noted, thanks.",
]
_DAYPARTS = ["today", "tomorrow", "this afternoon", "next week", "before EOD"]


def _fake_message():
    """Generate a short, chat-style message instead of a lorem-ipsum paragraph."""
    template = random.choice(_MESSAGE_TEMPLATES)
    return template.format(
        greeting=random.choice(_GREETINGS),
        sentence=fake.sentence(nb_words=random.randint(5, 12))[:-1],
        question=fake.sentence(nb_words=random.randint(4, 9))[:-1] + "?",
        topic=fake.bs(),
        name=fake.first_name(),
        daypart=random.choice(_DAYPARTS),
        reaction=random.choice(_REACTIONS),
    ).strip()


def coro(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        return asyncio.run(f(*args, **kwargs))
    return wrapper


async def _init_auth(url, login):
    auth_result = await authenticate(url, login, login)
    cookie = {"session_id": auth_result[0] if isinstance(auth_result, tuple) else auth_result}
    partner_id = (await make_orm_call(
        url, "res.partner", "search", [[["user_ids.login", "=", login]]], cookies=cookie
    ))["result"][0]
    return cookie, partner_id


@click.group()
@click.option("--server-url", "url", default="http://localhost:8069", show_default=True, help="Odoo server URL.")
@click.option("--guest-cookie", "guest_cookie", show_default=True, help="Guest session cookie (dgid).")
@click.option("--login", "login", default="admin", show_default=True, help="User login for authentication.")
@click.pass_context
def cli(ctx, url, login, guest_cookie=None):
    """Odoo Discuss data generation CLI."""
    ctx.ensure_object(dict)
    ctx.obj["server_url"] = url
    if guest_cookie:
        ctx.obj["cookie"] = {"dgid": guest_cookie}
    else:
        cookie, partner_id = asyncio.run(_init_auth(url, login))
        ctx.obj["cookie"] = cookie
        ctx.obj["partner_id"] = partner_id


async def _message(ctx, thread_id, thread_model, body=None):
    await make_rpc(
        f"{ctx.obj['server_url']}/mail/message/post",
        params={
            "post_data": {
                "attachment_ids": [],
                "body": (body if body is not None else _fake_message()),
                "canned_response_ids": [],
                "message_type": "comment",
                "partner_ids": [],
                "subtype_xmlid": "mail.mt_comment",
            },
            "thread_id": thread_id,
            "thread_model": thread_model,
        },
        cookies=ctx.obj["cookie"],
    )


@cli.command()
@click.pass_context
@click.option("-n", "number", type=int, required=True, help="Number of messages to create.")
@click.option("-i", "thread_id", type=int, required=True, show_default=True, help="Thread ID.")
@click.option("-m", "thread_model", default="discuss.channel", show_default=True, help="Thread model.")
@click.option("--index", "numbers", is_flag=True, help="Use the loop index as the message body.")
@click.option("--real", "real", is_flag=True, help="Use the real post API instead of a direct ORM call.")
@coro
async def message(ctx, number, thread_id, thread_model, numbers, real=None):
    """Create messages with random content in a thread."""
    BATCH_SIZE = 100
    if not real:
        for start in trange(
            0, number, BATCH_SIZE, desc=f"Creating messages (orm): id={thread_id}, model={thread_model}"
        ):
            end = min(start + BATCH_SIZE, number)
            batch = [
                {
                    "body": str(n) if numbers else _fake_message(),
                    "message_type": "comment",
                    "res_id": thread_id,
                    "model": thread_model,
                    "author_id": ctx.obj["partner_id"],
                }
                for n in range(start, end)
            ]
            await make_orm_call(
                ctx.obj["server_url"],
                "mail.message",
                "create",
                parameters=[batch],
            )
    else:
        for n in trange(int(number), desc=f"Sending messages (real): id={thread_id}, model={thread_model}"):
            await _message(ctx, thread_id, thread_model, body=str(n) if numbers else None)


PASSWORD = "ab!cd!E123!!"
LOGIN_PREFIX = "discuss-cli-"


@cli.command()
@click.pass_context
@click.option("-n", "number", type=int, required=True, help="Number of users to create.")
@coro
async def user(ctx, number):
    """Create users with random names."""
    return await _user(ctx, number)


async def _user(ctx, number):
    cache = await _get_avatar_cache()
    BATCH_SIZE = 10
    batches = []
    all_logs = []
    for start in range(0, number, BATCH_SIZE):
        logs = [f"{LOGIN_PREFIX}{uuid4()}" for _ in range(min(BATCH_SIZE, number - start))]
        all_logs.extend(logs)
        batches.append(logs)

    with tqdm(total=number, desc="Creating users") as pb:
        async def _create_batch(logs):
            result = await make_orm_call(
                ctx.obj["server_url"],
                "res.users",
                "create",
                [[{
                    "login": log,
                    "name": fake.name(),
                    "password": PASSWORD,
                    "image_1920": random.choice(cache),
                } for log in logs]],
                cookies=ctx.obj["cookie"],
            )
            pb.update(len(logs))
            return result["result"]

        results = await asyncio.gather(*[_create_batch(logs) for logs in batches])

    all_uids = [uid for batch_uids in results for uid in batch_uids]
    return all_logs, all_uids


@cli.command()
@click.pass_context
@click.option("-n", "number", type=int, required=True, help="Number of channels to create.")
@click.option("--owner", "owner", type=int, help="Parent channel ID. Creates sub-threads under it instead.")
@click.option("--message", "messages", type=int, default=0, help="Number of messages to post in each channel.")
@click.option(
    "--sub-thread",
    "subs",
    type=int,
    default=0,
    show_default=True,
    help="Number of sub-threads to create per channel.",
)
@click.option("--member", "members", type=int, default=0, help="Number of members to create and add to each channel.")
@click.option("--category", "categories", type=int, default=0, help="Number of categories to create and randomly assign channels to.")
@coro
async def channel(ctx, number, owner, subs, messages, members, categories):
    """Create channels with optional sub-threads/messages/members."""
    return await _channel(ctx, number, owner, subs, messages, members, categories)


async def _ensure_partners(ctx, number):
    """Return (logins, partner_ids), reusing existing partners before creating new users."""
    existing = (await make_orm_call(
        ctx.obj["server_url"],
        "res.users",
        "search_read",
        [[["login", "=like", f"{LOGIN_PREFIX}%"]], ["login", "partner_id"]],
        cookies=ctx.obj["cookie"],
    ))["result"]
    reused = existing[:number]
    reused_logins = [user["login"] for user in reused]
    reused_partner_ids = [user["partner_id"][0] for user in reused]
    missing = number - len(reused)
    if not missing:
        return reused_logins, reused_partner_ids
    new_logins, uids = await _user(ctx, missing)
    new_partner_ids = (await make_orm_call(
        ctx.obj["server_url"],
        "res.partner",
        "search",
        [[["user_ids", "in", uids]]],
        cookies=ctx.obj["cookie"],
    ))["result"]
    return reused_logins + new_logins, reused_partner_ids + new_partner_ids


async def _add_channel_members(ctx, channel_id, partner_ids, **kwargs):
    """Add members to a channel via the `/discuss/channel/add_members` store handler."""
    return await make_rpc(
        f"{ctx.obj['server_url']}/mail/store",
        {"fetch_params": [["/discuss/channel/add_members", {
            "channel_id": channel_id,
            "partner_ids": partner_ids,
            **kwargs,
        }]]},
        cookies=ctx.obj["cookie"],
    )


async def _channel(ctx, number, owner, subs, messages=None, members=0, categories=0):
    if owner and subs:
        raise Exception("Cannot create nested sub threads.")
    thread_ids = []
    if not owner:
        async def _create_one(pb):
            result = await make_rpc(
                f"{ctx.obj['server_url']}/mail/store",
                params={
                    "fetch_params": [
                        [
                            "/discuss/create_channel",
                            {
                                "group_id": 1,
                                "name": fake.catch_phrase(),
                                "is_readonly": False,
                            },
                            1,
                        ],
                    ]
                },
                cookies=ctx.obj["cookie"],
            )
            pb.update(1)
            return int(result["result"]["discuss.channel"][0]["id"])

        with tqdm(total=number, desc="Creating threads") as pb:
            thread_ids = list(await asyncio.gather(*[_create_one(pb) for _ in range(number)]))
    else:
        thread_ids = [owner] * number
        subs = 1
    await asyncio.sleep(0.5)
    subs = subs or 0
    if subs:
        with tqdm(total=len(thread_ids) * subs, desc="Creating sub threads") as pb:
            for owner in thread_ids:
                for _ in range(subs):
                    tid = int(
                        (await make_rpc(
                            f"{ctx.obj['server_url']}/discuss/channel/sub_channel/create",
                            params={"parent_channel_id": owner, "name": fake.catch_phrase()},
                            cookies=ctx.obj["cookie"],
                        ))["result"]["sub_channel"]
                    )
                    await _message(ctx, tid, "discuss.channel")
                    pb.update(1)

    if messages:
        with tqdm(total=len(thread_ids) * messages, desc="Creating messages") as pb:
            for _ in range(messages):
                for thread_id in thread_ids:
                    await _message(ctx, thread_id, "discuss.channel")
                    pb.update(1)

    if members:
        _, partner_ids = await _ensure_partners(ctx, members)
        with tqdm(total=len(thread_ids), desc="Adding members to channels") as pb:
            async def _add(thread_id):
                await _add_channel_members(ctx, thread_id, partner_ids)
                pb.update(1)
            await asyncio.gather(*[_add(tid) for tid in thread_ids])

    if categories and thread_ids:
        category_ids = (await make_orm_call(
            ctx.obj["server_url"],
            "discuss.category",
            "create",
            [[{"name": fake.catch_phrase()} for _ in range(categories)]],
            cookies=ctx.obj["cookie"],
        ))["result"]
        buckets = {}
        for thread_id in thread_ids:
            buckets.setdefault(random.choice(category_ids), []).append(thread_id)
        with tqdm(total=len(buckets), desc="Assigning channels to categories") as pb:
            async def _assign(cat_id, chan_ids):
                await make_orm_call(
                    ctx.obj["server_url"],
                    "discuss.channel",
                    "write",
                    parameters=[chan_ids, {"discuss_category_id": cat_id}],
                    cookies=ctx.obj["cookie"],
                )
                pb.update(1)
            await asyncio.gather(*[_assign(cat_id, chan_ids) for cat_id, chan_ids in buckets.items()])

    return thread_ids


@cli.command()
@click.pass_context
@click.option("-n", "number", type=int, required=True, help="Number of call participants.")
@coro
async def call(ctx, number):
    """Create a channel and simulate users joining a call."""
    thread_id, = await _channel(ctx, number=1, owner=None, subs=None)
    logins, partner_ids = await _ensure_partners(ctx, number)
    await _add_channel_members(ctx, thread_id, partner_ids)
    with tqdm(total=len(logins), desc="Adding users to the call") as pb:
        async def _join(login):
            auth_result = await authenticate(ctx.obj["server_url"], login, PASSWORD)
            cookie = {"session_id": auth_result[0] if isinstance(auth_result, tuple) else auth_result}
            await make_rpc(
                f"{ctx.obj['server_url']}/mail/rtc/channel/join_call",
                {"channel_id": thread_id},
                cookies=cookie,
            )
            pb.update(1)
        await asyncio.gather(*[_join(login) for login in logins])


@cli.command()
@click.pass_context
@click.option("-n", "number", type=int, required=True, help="Number of members to create and add.")
@click.option("-t", "thread_id", type=int, required=True, help="Channel ID to add members to.")
@click.option("-s", "seen_message_id", type=int, help="Mark this message as seen for each new member.")
@coro
async def member(ctx, number, thread_id, seen_message_id):
    """Create users and add them as members of a channel."""
    logins, partner_ids = await _ensure_partners(ctx, number)
    print(await _add_channel_members(ctx, thread_id, partner_ids))
    if not seen_message_id:
        return
    async def _mark_seen(login):
        auth_result = await authenticate(ctx.obj["server_url"], login, PASSWORD)
        cookie = {"session_id": auth_result[0] if isinstance(auth_result, tuple) else auth_result}
        await make_rpc(
            f"{ctx.obj['server_url']}/discuss/channel/mark_as_read",
            {"channel_id": thread_id, "last_message_id": seen_message_id},
            cookies=cookie,
        )
    await asyncio.gather(*[_mark_seen(login) for login in logins])


@cli.command()
@click.pass_context
@click.option("-n", "num_emojis", type=int, required=True, help="Number of distinct emojis to use.")
@click.option("--partner", "num_partners", type=int, required=True, help="Number of users to create as reactors.")
@click.option("-m", "message_id", type=int, required=True, help="ID of the message to react to.")
@click.option("--chance", "chance", type=float, default=0.5, show_default=True, help="Probability each user reacts with a given emoji.")
@coro
async def reaction(ctx, num_emojis, num_partners, message_id, chance):
    """Add random emoji reactions to a message."""
    logins, _ = await _user(ctx, num_partners)
    random_emojis = random_emoji(count=num_emojis)
    assert len(random_emojis) == num_emojis
    reacted = set()
    reaction_by_login = {}
    for emoji in random_emojis:
        for login in logins:
            if emoji in reacted and random.random() > chance:
                continue
            reaction_by_login.setdefault(login, []).append(emoji)
            reacted.add(emoji)

    async def _react(login, emojis):
        auth_result = await authenticate(ctx.obj["server_url"], login, PASSWORD)
        cookie = {"session_id": auth_result[0] if isinstance(auth_result, tuple) else auth_result}
        for emoji in emojis:
            await make_rpc(
                f"{ctx.obj['server_url']}/mail/message/reaction",
                {"message_id": message_id, "action": "add", "content": emoji},
                cookies=cookie,
            )

    await asyncio.gather(*[_react(login, emojis) for login, emojis in reaction_by_login.items()])


@cli.command()
@click.pass_context
@click.option("-t", "--thread-id", type=int, required=True, help="Channel ID to post the poll in.")
@click.option("-m", "--thread-model", type=str, default="discuss.channel", show_default=True, help="Thread model.")
@click.option("-v", "--vote", "votes", type=int, default=0, help="Number of users to create and simulate voting.")
@click.option("-d", "--duration", type=int, default=60, show_default=True, help="Poll duration in minutes.")
@coro
async def poll(ctx, thread_id, thread_model, votes, duration):
    """Create a poll in a channel and optionally simulate votes."""
    poll_templates = [
        {
            "question": "What's your favorite food?",
            "options": ["🍔 Burger", "🍣 Sushi", "🍕 Pizza", "🥗 Salad"]
        },
        {
            "question": "What's your favorite color?",
            "options": ["🔴 Red", "🟢 Green", "🔵 Blue", "🟡 Yellow"]
        },
        {
            "question": "What's your favorite movie genre?",
            "options": ["🎬 Action", "😂 Comedy", "❤️ Romance", "👻 Horror"]
        },
        {
            "question": "What's your favorite programming language?",
            "options": ["🐍 Python", "☕ Java", "💎 Ruby", "🟦 C#"]
        },
        {
            "question": "Which season do you like most?",
            "options": ["❄️ Winter", "🌸 Spring", "☀️ Summer", "🍂 Autumn"]
        },
    ]
    poll_data = random.choice(poll_templates)
    question = poll_data["question"]
    options = poll_data["options"]
    poll_id = (await make_rpc(
        f"{ctx.obj['server_url']}/mail/poll/create",
        params={
            "duration": duration,
            "option_labels": options,
            "question": question,
            "thread_id": thread_id,
            "thread_model": thread_model,
            "allow_multiple_options": True,
        },
        cookies=ctx.obj["cookie"],
    ))["result"]
    click.echo(f"Poll created with ID: {poll_id}")
    click.echo(f"Question: {question}")
    click.echo(f"Options: {', '.join(options)}")
    if votes > 0:
        logins, _ = await _user(ctx, votes)
        poll_options = (await make_orm_call(
            ctx.obj["server_url"],
            "mail.poll.option",
            "search_read",
            [[["poll_id", "=", poll_id]], ["id"]],
            cookies=ctx.obj["cookie"],
        ))["result"]

        with tqdm(total=len(logins), desc="Voting on poll") as pb:
            async def _vote(login):
                auth_result = await authenticate(ctx.obj["server_url"], login, PASSWORD)
                cookie = {"session_id": auth_result[0] if isinstance(auth_result, tuple) else auth_result}
                chosen_options = random.sample(poll_options, k=random.randint(1, len(poll_options)))
                option_ids = [opt["id"] for opt in chosen_options]
                await make_rpc(
                    f"{ctx.obj['server_url']}/mail/poll/vote",
                    params={"poll_id": poll_id, "option_ids": option_ids},
                    cookies=cookie,
                )
                pb.update(1)
            await asyncio.gather(*[_vote(login) for login in logins])

    click.echo("Poll setup complete!")


if __name__ == "__main__":
    cli()
