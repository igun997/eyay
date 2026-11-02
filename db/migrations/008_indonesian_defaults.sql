-- Indonesian becomes the default language. Seeded names are translated only while they still hold the original English seed text, so
-- anything an admin renamed is left alone. Text the worker writes (event titles, timeline) follows engine_settings 'defaults' -> language
-- (default 'id'). The questions sent to the optional second AI check stay English on purpose: that is the language the model works in.
UPDATE engine_settings SET value = value || '{"language":"id"}'::jsonb WHERE key = 'defaults' AND NOT (value ? 'language');

UPDATE categories SET title = 'Orang'       WHERE label = 'person'     AND title = 'Person';
UPDATE categories SET title = 'Mobil'       WHERE label = 'car'        AND title = 'Car';
UPDATE categories SET title = 'Truk'        WHERE label = 'truck'      AND title = 'Truck';
UPDATE categories SET title = 'Bus'         WHERE label = 'bus'        AND title = 'Bus';
UPDATE categories SET title = 'Sepeda motor' WHERE label = 'motorcycle' AND title = 'Motorcycle';
UPDATE categories SET title = 'Sepeda'      WHERE label = 'bicycle'    AND title = 'Bicycle';

UPDATE behavior_rules SET title = 'Benda mirip senjata', description = 'Seseorang memegang pistol, pisau, linggis, atau benda serupa.'
  WHERE key = 'weapon' AND title = 'Weapon-like object';
UPDATE behavior_rules SET title = 'Mengutak-atik peralatan, kunci, atau pintu', description = 'Seseorang memaksa, membobol, atau mengganggu pintu, kunci, gerbang, mesin, atau kamera.'
  WHERE key = 'tampering' AND title = 'Tampering with equipment, lock or door';
UPDATE behavior_rules SET title = 'Menyembunyikan barang', description = 'Seseorang menyembunyikan barang di tas atau pakaian (kemungkinan pencurian).'
  WHERE key = 'concealment' AND title = 'Concealing an item';
UPDATE behavior_rules SET title = 'Memanjat atau melewati pembatas', description = 'Seseorang memanjat pagar, tembok, atau gerbang.'
  WHERE key = 'trespass' AND title = 'Climbing or crossing a barrier';
UPDATE behavior_rules SET title = 'Wajah tertutup atau disamarkan',
  description = 'Masker, balaklava, atau tudung yang menutupi wajah. Mati secara default: sering menyala pada topi dan tudung di rekaman biasa (terukur 4 dari 12 orang pada 0,75); nyalakan dan sesuaikan ambangnya pada rekaman Anda sendiri.'
  WHERE key = 'face_covered' AND title = 'Face covered or disguised';
UPDATE behavior_rules SET title = 'Berdiam di area pandang', description = 'Seseorang diam di tempat selama seluruh foto lanjutan (kemungkinan berkeliaran atau mengintai).'
  WHERE key = 'lingering' AND title = 'Lingering in view';
UPDATE behavior_rules SET title = 'Plat nomor tertutup atau disamarkan', description = 'Plat disembunyikan atau tidak ada. Mati secara default: sangat bergantung pada sudut kamera.'
  WHERE key = 'plate_obscured' AND title = 'Licence plate covered or obscured';
UPDATE behavior_rules SET title = 'Pisau atau benda mirip bilah di dekat orang',
  description = 'AI kamera melihat pisau, gunting, atau tongkat di dekat seseorang. Berjalan tanpa pemeriksaan AI kedua; benda kecil atau tersembunyi sering terlewat, dan suasana dapur atau kantor bisa memicu peringatan palsu.'
  WHERE key = 'weapon_object' AND title = 'Knife or blade-like object near a person';
