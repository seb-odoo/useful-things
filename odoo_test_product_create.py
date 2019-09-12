
import logging
import time

from random_words import RandomWords

import odoo

_logger = logging.getLogger(__name__)

rw = RandomWords()


def execute(self, product_nb=1000):

    odoo.cli.server.setup_pid_file()

    vals_list = [{
        'name': name,
    } for name in rw.random_words(count=product_nb)]

    _logger.info('PID set, 3 sec given to start pyflame')

    time.sleep(3)

    _logger.info('Starting to create products')

    start = time.time()

    products = self.env['product.template'].create(vals_list)

    products.flush()

    _logger.info('Done creating %d products in %.3fs' % (len(products), time.time() - start))


if __name__ == "__main__":
    execute(self)
