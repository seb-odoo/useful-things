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
