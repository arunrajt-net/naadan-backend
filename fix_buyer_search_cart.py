content = open('frontend/src/pages/BuyerSearch.jsx', 'r', encoding='utf-8').read()

# Fix 2: FLOATING CART TRIGGER - move up above bottom nav 
old_cart = (
    '        \u003cbutton \n'
    '          onClick={() => setIsCartOpen(true)}\n'
    '          className="fixed bottom-24 right-6 z-[1000] bg-green-700 hover:bg-green-800 text-white font-extrabold rounded-full w-16 h-16 shadow-2xl flex items-center justify-center border-none cursor-pointer scale-110 animate-bounce"\n'
    '        >'
)

new_cart = (
    '        <button \n'
    '          onClick={() => setIsCartOpen(true)}\n'
    '          className="fixed right-5 z-[1002] bg-green-700 hover:bg-green-800 text-white font-extrabold rounded-full w-16 h-16 shadow-2xl flex items-center justify-center border-none cursor-pointer cart-fab-pulse"\n'
    '          style={{ bottom: "calc(72px + env(safe-area-inset-bottom) + 80px)" }}\n'
    '          title="Open Cart"\n'
    '          aria-label="Open shopping cart"\n'
    '        >'
)

if old_cart in content:
    content = content.replace(old_cart, new_cart, 1)
    print('Cart button fixed!')
else:
    print('Cart button NOT FOUND')
    idx = content.find('FLOATING CART TRIGGER')
    print(repr(content[idx:idx+500]))

open('frontend/src/pages/BuyerSearch.jsx', 'w', encoding='utf-8').write(content)
