
import logging
import math
import random
import time

from random_words import RandomWords

import odoo

_logger = logging.getLogger(__name__)

rw = RandomWords()


def execute(self, template_nb=10000):

    guaranteed_max = 10
    combination_max_nb = 1000
    nb_values_per_attribute = 20

    odoo.cli.server.setup_pid_file()

    _logger.info('PID set, 3 sec given to start pyflame')

    time.sleep(3)

    # ---------- create attributes -----------------------------------------

    _logger.info('Now creating attributes')

    start = time.time()

    product_attributes = self.env['product.attribute']
    for attribute in rw.random_words(count=100):
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
            } for name in rw.random_words(count=nb_values_per_attribute)])

        product_attributes += product_attribute

    product_attributes.flush()

    # ---------- create new ------------------------------------------------

    _logger.info('Done with attributes in %.3fs, now starting to create %s products' % (time.time() - start, template_nb))

    start = time.time()

    products = []
    # TODO SEB handle sequence
    for i in range(template_nb):
        products.append({
            'name': "p %s" % rw.random_word(),
            'website_published': True,
        })
    product_templates = self.env['product.template'].with_context(create_product_product=True, tracking_disable=True).create(products)

    product_templates.flush()

    # ---------- adding ptal -----------------------------------------------

    _logger.info('Done creating %d products in %.3fs, now adding ptal' % (len(products), time.time() - start))

    start = time.time()

    lines = []

    index = 0
    for product_template in product_templates:
        index += 1
        # This allows to have a nice distribution of variants where few products
        # have a lot of variants and a lot of products have few variants.
        if index < guaranteed_max:
            max_attribute_nb = combination_max_nb
        else:
            max_attribute_nb = max(1, int(math.ceil(combination_max_nb / (1 + index - guaranteed_max))))

        remaining_attributes = product_attributes

        while max_attribute_nb >= 2 and remaining_attributes:
            value_nb = random.randint(1, min(nb_values_per_attribute, max_attribute_nb))
            product_attribute = random.sample(remaining_attributes, 1)[0]
            remaining_attributes -= product_attribute
            lines += [{
                'attribute_id': product_attribute.id,
                'product_tmpl_id': product_template.id,
                'value_ids': [(6, 0, [v.id for v in random.sample(product_attribute.value_ids, value_nb)])],
            }]
            max_attribute_nb = max_attribute_nb // value_nb

    lines = self.env['product.template.attribute.line'].create(lines)

    lines.flush()

    _logger.info('Done creating %d ptal in %.3fs' % (len(lines), time.time() - start))

    self.env.cr.commit()


if __name__ == "__main__":
    execute(self)
