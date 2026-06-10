content = open('backend/routes_orders.py', 'r', encoding='utf-8').read()
search = '    pickup_landmark = farmer.pickup_landmark if farmer else ""'
replace = '    pickup_landmark = farmer.pickup_landmark if farmer else ""\n    buyer_name = buyer.name if buyer else "Unknown Buyer"'
if search in content:
    content = content.replace(search, replace, 1)
    open('backend/routes_orders.py', 'w', encoding='utf-8').write(content)
    print('Fixed!')
else:
    print('NOT FOUND')
