from random import randint
from random_words import RandomWords

rw = RandomWords()

print('executing stuff')

total = 300
categs = [{
    'name': rw.random_word(),
} for i in range(total)]

categories = env['product.public.category'].create(categs)

print(categories)

parents = categories

for cat in categories:
    parent = parents[randint(0, len(parents) - 1)]
    try:
        if parent != cat:
            cat.write({'parent_id': parent.id})
            # Categories that already have a parent can't be assigned as parent
            # of others. A parent hierarchy will still be created when children
            # are assigned first.
            # This prevents SQL lock or something like that.
            parents -= cat
        else:
            print('ignoring self')
    except Exception:
        print('parent failed')

for product in env['product.template'].search([]):
    cat_nbr = randint(0, 4)
    for i in range(cat_nbr):
        product.public_categ_ids += categories[randint(0, len(categories) - 1)]


print('commiting')

env.cr.commit()
