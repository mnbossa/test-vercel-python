import requests
import xml.etree.ElementTree as ET
import json
import csv

def get_mep_data(output_format='json'):
    # Official EP XML endpoint
    url = "https://www.europarl.europa.eu/meps/en/full-list/xml"
    
    # Fetch data
    response = requests.get(url)
    response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)

    # Parse XML data
    try:
        root = ET.fromstring(response.content)
    except ET.ParseError as e:
        print(f"Error parsing XML: {e}")
        print("Response content that caused the error:")
        print(response.text[:500]) 
        return []

    # Process MEP information
    meps_list = []
    # The structure of the XML needs to be inspected to get the correct paths.
    # Assuming the XML structure is something like:
    # <meps>
    #   <mep>
    #     <id>...</id>
    #     <fullName>...</fullName>
    #     <country>...</country>
    #     <politicalGroup>...</politicalGroup>
    #     <nationalPoliticalGroup>...</nationalPoliticalGroup>
    #   </mep>
    # </meps>
    
    # print(response.text[:1000]) # inspect XML structure 

    for mep_node in root.findall('.//mep'):
        try:
            full_name = mep_node.find('fullName').text if mep_node.find('fullName') is not None else 'N/A'
            country_node = mep_node.find('country')
            country = country_node.text if country_node is not None else 'N/A'
            
            political_group_node = mep_node.find('politicalGroup')
            political_group = political_group_node.text if political_group_node is not None else 'N/A'
            
            national_party_node = mep_node.find('nationalPoliticalGroup')
            national_party = national_party_node.text if national_party_node is not None else 'N/A'

            mep_data = {
                'name': full_name,
                'country': country,
                'political_group': political_group,
                'national_party': national_party
            }
            meps_list.append(mep_data)
        except AttributeError as e:
            print(f"Skipping an MEP due to missing data or unexpected structure: {e}")
            continue
            
    if not meps_list:
        print("No MEP data could be extracted. This might be due to an incorrect XML structure assumption.")
        print("Please inspect the XML structure from the URL or the printed snippet.")

    # Save to file
    if meps_list:
        if output_format == 'json':
            with open('data/meps.json', 'w', encoding='utf-8') as f:
                json.dump(meps_list, f, indent=2, ensure_ascii=False)
            print("Data saved to meps.json")
        elif output_format == 'csv':
            with open('meps.csv', 'w', newline='', encoding='utf-8') as f:
                if meps_list:
                    writer = csv.DictWriter(f, fieldnames=meps_list[0].keys())
                    writer.writeheader()
                    writer.writerows(meps_list)
                    print("Data saved to meps.csv")
                else:
                    print("No data to write to CSV.")
    else:
        print("No MEPs data to save.")
        
    return meps_list

meps = get_mep_data(output_format='json')
