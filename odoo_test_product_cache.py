
import logging
import time

from random_words import RandomWords

import odoo

_logger = logging.getLogger(__name__)

rw = RandomWords()


def execute(self):

    odoo.cli.server.setup_pid_file()

    _logger.info('PID set, 3 sec given to start pyflame')

    time.sleep(3)

    _logger.info('Starting')

    product_templates = self.env['product.template'].search([])
    product_templates[:1].name
    products = product_templates.mapped('product_variant_ids')
    products[:1].active

    _logger.info('Really Starting')

    start = time.time()

    products.write({'active': False})

    product_templates[:1].name

    _logger.info('Done for %d templates %d variants in %.3fs' % (len(product_templates), len(products), time.time() - start))

    # self.env.cr.commit()


if __name__ == "__main__":
    execute(self)
