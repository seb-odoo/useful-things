// Copying this code in the browser console will create `amount` users.
(async function () {
    const env = odoo.__WOWL_DEBUG__.root.env
    const amount = 100;
    const [lastId] = await env.services.orm.search(
        'res.users',
        [],
        {
            order: 'id desc',
            limit: 1,
        },
    );
    env.services.orm.create(
        'res.users',
        Array.from({ length: amount }, (v, k) => k + 1).map(i => {
            return {
                'name': `user ${i + lastId}`,
                'login': `user ${i + lastId}`,
            };
        }),
    );
})();

// Copying this code in the browser console will add `amount` messages on the (first) chatter.
odoo.define(async function (require) {
    const chatter = odoo.__DEBUG__.messaging.models['Chatter'].all()[0];
    const amount = 100;
    const promises = [];
    for (let i = 0; i < amount; i++) {
        const params = {
            'post_data': {
                'body': `test ${i}`,
            },
            'thread_id': chatter.thread.id,
            'thread_model': chatter.thread.model,
        };
        promises.push(chatter.messaging.rpc({ route: `/mail/message/post`, params }));
    }
    await Promise.all(promises);
    chatter.refresh();
});

// Copying this code in the browser console will create/open `amount` chat with last created users.
odoo.define(async function (require) {
    const amount = 300;
    const usersWithExistingChats = odoo.__DEBUG__.messaging.models["Thread"].all(t => t.channel_type === 'chat').map(t => t.correspondent.user.id);
    const userIds = await odoo.__DEBUG__.messaging.rpc({
        model: 'res.users',
        method: 'search',
        args: [
            [ // domain
                ['id', 'not in', usersWithExistingChats],
            ],
        ],
        kwargs: {
            order: 'id desc',
            limit: amount,
        },
    });
    for (const userId of userIds) {
        odoo.__DEBUG__.messaging.rpc({
            model: 'discuss.channel',
            method: 'channel_get',
            kwargs: {
                partners_to: [userId],
            },
        });
    }
});

// Copying this code in the browser console will add `amount` users in the currently open channel in discuss.
odoo.define(async function (require) {
    const amount = 100;
    const channel = odoo.__DEBUG__.messaging.discuss.thread;
    const partnerIds = await odoo.__DEBUG__.messaging.rpc({
        model: 'res.partner',
        method: 'search',
        kwargs: {
            domain: [
                ['channel_ids', 'not in', [channel.id]],
                ['user_ids', '!=', false],
            ],
            order: 'id desc',
            limit: amount,
        },
    });
    odoo.__DEBUG__.messaging.rpc({
        model: 'discuss.channel',
        method: 'add_members',
        args: [[channel.id]],
        kwargs: {
            partner_ids: partnerIds,
        },
    });
});

// Copying this code in the browser console will create `amount` public users (to simulate strange data in our prod...).
odoo.define(async function (require) {
    const amount = 1000;
    const [lastId] = await odoo.__DEBUG__.messaging.rpc({
        model: 'res.users',
        method: 'search',
        args: [
            [ // domain
            ],
        ],
        kwargs: {
            order: 'id desc',
            limit: 1,
        },
    });
    odoo.__DEBUG__.messaging.rpc({
        model: 'res.users',
        method: 'create',
        args: [
            Array.from({ length: amount }, (v, k) => k + 1).map(i => {
                return {
                    'name': `user ${i + lastId}`,
                    'login': `user ${i + lastId}`,
                    'sel_groups_1_9_10': 10, // public
                };
            }),
        ],
    });
});

// Rename regexx::

// click\("(.*):contains\(([^(]*)\)"\);
// click("$1", { text: "$2" });

// contains\("(.*):contains\(([^(]*)\)"\);
// contains("$1", 1, { text: "$2" });

// contains\("(.*):contains\(([^(]*)\)", (.*)\);
// contains("$1", $3, { text: "$2" });



// github click load more
buttons = [...document.querySelectorAll(".js-button-text")].filter(el => el.textContent === "Load diff");
buttons.length = 20;
buttons.forEach(el => el.click());
