// Copying this code in the browser console will create `amount` users.
odoo.define(async function (require) {
    const env = require('web.env');
    const amount = 100;
    const [lastId] = await env.services.rpc({
        model: 'res.users',
        method: 'search',
        kwargs: {
            args: [],
            order: 'id desc',
            limit: 1,
        },
    });
    env.services.rpc({
        model: 'res.users',
        method: 'create',
        args: [
            Array.from({ length: amount }, (v, k) => k + 1).map(i => {
                return {
                    'name': `user ${i + lastId}`,
                    'login': `user ${i + lastId}`,
                };
            }),
        ],
    });
});

// Copying this code in the browser console will add `amount` messages on the (first) chatter.
odoo.define(async function (require) {
    const chatter = odoo.__DEBUG__.messaging.models['mail.chatter'].all()[0];
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
        promises.push(chatter.messaging.env.services.rpc({ route: `/mail/message/post`, params }));
    }
    await Promise.all(promises);
    chatter.refresh();
});
