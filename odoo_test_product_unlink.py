
import logging
import random
import time

from random_words import RandomWords

import odoo

_logger = logging.getLogger(__name__)

rw = RandomWords()


def execute(self):

    odoo.cli.server.setup_pid_file()

    _logger.info('Starting')

    product_attributes = self.env['product.attribute']
    for attribute in rw.random_words(count=3):
        # create the product attribute
        product_attribute = product_attributes.search([('name', '=', attribute)])
        if not product_attribute:
            product_attribute = product_attributes.create({
                'name': "pa %s" % attribute,
                'create_variant': 'always',
            })
            # create the product attribute value
            self.env['product.attribute.value'].create([{
                'name': "pav %s" % name,
                'attribute_id': product_attribute.id,
            } for name in rw.random_words(count=10)])

        product_attributes += product_attribute

    product_template = self.env['product.template'].with_context(create_product_product=True, tracking_disable=True).create({
        'name': "p %s" % rw.random_word(),
        'website_published': True,
    })

    self.env['product.template.attribute.line'].create({
        'attribute_id': pa.id,
        'product_tmpl_id': product_template.id,
        'value_ids': [(6, 0, random.sample(pa.value_ids.ids, 10))],
    } for pa in product_attributes)

    _logger.info('Create variants')

    product_template.with_context(tracking_disable=True).create_variant_ids()
    variants = product_template.product_variant_ids

    # Create a dummy SO to prevent the variant from being deleted by
    # _unlink_or_archive() because the variant is a related field that
    # is required on the SO line
    so = self.env['sale.order'].create({'partner_id': 1})
    self.env['sale.order.line'].create({
        'order_id': so.id,
        'name': "test",
        'product_id': variants[:1].id
    })

    _logger.info('PID set, 3 sec given to start pyflame')
    time.sleep(3)

    _logger.info('Deleting')

    start = time.time()

    variants.with_context()._unlink_or_archive()

    _logger.info('Done deleting %d variants in %.3fs' % (len(variants), time.time() - start))
    # self.env.cr.commit()


if __name__ == "__main__":
    execute(self)
