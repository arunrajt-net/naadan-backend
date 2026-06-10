content = open('frontend/src/components/GlobalAIAssistant.jsx', 'r', encoding='utf-8').read()

old_pos = '<div className="fixed bottom-6 right-6 z-[9999] font-sans">'
new_pos = '<div className="fixed right-5 z-[9999] font-sans" style={{ bottom: "calc(72px + env(safe-area-inset-bottom) + 16px)" }}>'

if old_pos in content:
    content = content.replace(old_pos, new_pos, 1)
    print('GlobalAI position fixed!')
else:
    print('NOT FOUND')
    idx = content.find('fixed bottom')
    print(repr(content[idx:idx+200]))

open('frontend/src/components/GlobalAIAssistant.jsx', 'w', encoding='utf-8').write(content)
