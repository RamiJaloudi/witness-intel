"""Curated Color-Hex popular palettes, with readable derived UI shades."""
import os

CATALOG=[
 ('classic','Workbench Classic',['#274463','#0047ba','#ff8700','#f8f9fa','#202731'],None),
 ('beautiful-blues','Beautiful Blues',['#011f4b','#03396c','#005b96','#6497b1','#b3cde0'],1294),
 ('cappuccino','Cappuccino',['#4b3832','#854442','#fff4e6','#3c2f2f','#be9b7b'],389),
 ('teal','Shades of Teal',['#b2d8d8','#66b2b2','#008080','#006666','#004c4c'],4666),
 ('faded-rose','Faded Rose',['#dfdfde','#a2798f','#d7c6cf','#8caba8','#ebdada'],2115),
 ('purple','Shades of Purple',['#efbbff','#d896ff','#be29ec','#800080','#660066'],1835),
 ('dusty-sage','Dusty Sage',['#f8d3c5','#fceee9','#dde6d5','#a3b899','#667b68'],71279),
 ('beach','Beach',['#96ceb4','#ffeead','#ff6f69','#ffcc5c','#88d8b0'],895),
 ('retro','Retro',['#666547','#fb2e01','#6fcb9f','#ffe28a','#fffeb3'],165),
]

def rgb(color):return [int(color[i:i+2],16)/255 for i in (1,3,5)]
def hex_color(values):return '#'+''.join(f'{round(max(0,min(1,v))*255):02x}' for v in values)
def luminance(color):
    values=[v/12.92 if v<=.04045 else ((v+.055)/1.055)**2.4 for v in rgb(color)]
    return sum(v*w for v,w in zip(values,[.2126,.7152,.0722]))
def contrast(a,b):
    high,low=sorted([luminance(a),luminance(b)],reverse=True);return (high+.05)/(low+.05)
def blend(color,white):return hex_color([v*(1-white)+white for v in rgb(color)])
def readable(color,minimum=4.5):
    while contrast(color,'#ffffff')<minimum:color=hex_color([v*.94 for v in rgb(color)])
    return color

def palettes():
    result=[]
    for key,name,colors,source_id in CATALOG:
        ranked=sorted(colors,key=luminance)
        primary=readable(colors[2] if key in {'beautiful-blues','teal','purple'} else colors[1] if key in {'cappuccino','faded-rose','retro'} else ranked[0])
        header=readable(ranked[0],7)
        tokens={'header':header,'primary':primary,'accent':colors[2] if key in {'cappuccino','beach'} else colors[-2],
                'page':blend(ranked[-1],.65),'panel':blend(ranked[-1],.82),'tint':blend(primary,.94),'line':blend(header,.82),'ink':'#202731','muted':'#52606d','on-primary':'#ffffff'}
        if key=='classic':tokens.update(header='#274463',primary='#0047ba',accent='#ff8700',page='#f8f9fa',panel='#f8f9fa',tint='#edf3ff',line='#dadce0')
        result.append({'id':key,'name':name,'colors':colors,'tokens':tokens,'source':f'https://www.color-hex.com/color-palette/{source_id}' if source_id else None})
    return result

def selected_palette():
    value=os.getenv('UI_PALETTE','classic')
    return value if any(row[0]==value for row in CATALOG) else 'classic'
