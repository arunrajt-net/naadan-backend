content = open('frontend/src/pages/BuyerSearch.jsx', 'r', encoding='utf-8').read()

# Fix 1: MOBILE FLOATING TOGGLE - move up above bottom nav
old_toggle = (
    '      {/* MOBILE FLOATING TOGGLE */}\n'
    '      <div className="lg:hidden fixed bottom-6 left-1/2 -translate-x-1/2 z-[1000] flex gap-3">\n'
    '         <motion.button \n'
    '           whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}\n'
    '           onClick={() => setViewMode(viewMode === \'map\' ? \'list\' : \'map\')}\n'
    '           className="bg-gray-900 text-white font-bold py-3.5 px-6 rounded-full shadow-2xl flex items-center gap-2 border-none cursor-pointer text-sm"\n'
    '         >\n'
    '           {viewMode === \'map\' ? <List size={16} /> : <Map size={16} />}\n'
    '           {viewMode === \'map\' ? \'List\' : \'Map\'}\n'
    '         </motion.button>\n'
    '      </div>'
)

new_toggle = (
    '      {/* MOBILE FLOATING MAP/LIST TOGGLE - floats above bottom nav */}\n'
    '      <div className="lg:hidden fixed left-1/2 -translate-x-1/2 z-[1001]" style={{ bottom: "calc(72px + env(safe-area-inset-bottom) + 16px)" }}>\n'
    '         <motion.button\n'
    '           initial={{ opacity: 0, y: 20, scale: 0.85 }}\n'
    '           animate={{ opacity: 1, y: 0, scale: 1 }}\n'
    '           transition={{ type: "spring", stiffness: 400, damping: 28, delay: 0.2 }}\n'
    '           whileHover={{ scale: 1.06 }}\n'
    '           whileTap={{ scale: 0.94 }}\n'
    '           onClick={() => setViewMode(viewMode === \'map\' ? \'list\' : \'map\')}\n'
    '           className="map-fab-toggle flex items-center gap-2.5 border-none cursor-pointer text-sm"\n'
    '           title={viewMode === \'map\' ? \'View as list\' : \'View nearby farms on map\'}\n'
    '           aria-label={viewMode === \'map\' ? \'Switch to list view\' : \'Open map \xe2\x80\x93 View Nearby Farms\'}\n'
    '         >\n'
    '           {viewMode === \'map\'\n'
    '             ? <><List size={18} strokeWidth={2.5} /><span>List View</span></>\n'
    '             : <><Map size={18} strokeWidth={2.5} /><span>View Nearby Farms</span></>\n'
    '           }\n'
    '         </motion.button>\n'
    '      </div>'
)

if old_toggle in content:
    content = content.replace(old_toggle, new_toggle)
    print('Toggle fixed!')
else:
    print('Toggle NOT FOUND')
    idx = content.find('MOBILE FLOATING TOGGLE')
    print(repr(content[idx:idx+600]))

open('frontend/src/pages/BuyerSearch.jsx', 'w', encoding='utf-8').write(content)
